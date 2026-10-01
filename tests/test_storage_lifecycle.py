#!/usr/bin/env python3
"""存储生命周期治理单元测试（成本治理 P0-1）。

覆盖评估报告 P0-1 的验收标准：
1. outputs/ 保留策略清理（年龄规则 / 数量上限 / 占位文件豁免 / 临时帧目录回收 / 禁用时零操作）
2. history.max_records 落实（超出自动裁剪最旧记录 / FTS 索引同步 / 禁用与下限语义）
3. 任务启动前磁盘空间预检（阈值语义 / 507 拒绝 / 放行路径）

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

import os
import sqlite3
import time

import pytest

from app.integrated_app.exceptions import DiskSpaceError
from app.integrated_app.history_db import HistoryDB, HistoryRecord
from app.integrated_app.routes.restore.common import ensure_disk_space
from app.integrated_app.services.output_retention import cleanup_outputs_once, cleanup_uploads_once


def _make_file(path: str, mtime: float | None = None) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"x" * 128)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


class TestCleanupOutputsOnce:
    """outputs/ 保留策略清理。"""

    def test_disabled_when_both_rules_zero(self, tmp_path):
        _make_file(str(tmp_path / "image" / "a.png"))
        removed, _freed = cleanup_outputs_once(str(tmp_path), max_age_days=0, max_files=0)
        assert removed == 0
        assert (tmp_path / "image" / "a.png").exists()

    def test_age_rule_removes_only_old_files(self, tmp_path):
        now = time.time()
        old_path = tmp_path / "image" / "old.png"
        new_path = tmp_path / "image" / "new.png"
        _make_file(str(old_path), mtime=now - 30 * 86400)
        _make_file(str(new_path), mtime=now)

        removed, freed = cleanup_outputs_once(str(tmp_path), max_age_days=14, max_files=0)

        assert removed == 1
        assert freed == 128
        assert not old_path.exists()
        assert new_path.exists()

    def test_age_rule_keeps_gitkeep(self, tmp_path):
        now = time.time()
        keep = tmp_path / "image" / ".gitkeep"
        _make_file(str(keep), mtime=now - 365 * 86400)

        cleanup_outputs_once(str(tmp_path), max_age_days=14, max_files=0)

        assert keep.exists()

    def test_age_rule_recycles_stale_frames_dir_keeps_active(self, tmp_path):
        """残留 _frames 目录按年龄整树回收；正在写入（mtime 新鲜）的不动。"""
        now = time.time()
        stale_frames = tmp_path / "video" / "_frames"
        active_frames = tmp_path / "video" / "_frames_active"
        _make_file(str(stale_frames / "frame_000000.png"), mtime=now - 30 * 86400)
        _make_file(str(active_frames / "frame_000000.png"), mtime=now)

        removed, _freed = cleanup_outputs_once(str(tmp_path), max_age_days=14, max_files=0)

        # 残留帧目录整树回收：目录内陈旧文件 + 目录本身
        assert not stale_frames.exists()
        assert active_frames.exists()
        assert removed == 1  # 帧文件按年龄规则删除计数

    def test_max_files_keeps_newest(self, tmp_path):
        now = time.time()
        for i in range(5):
            _make_file(str(tmp_path / "image" / f"img_{i}.png"), mtime=now - (10 - i) * 3600)

        removed, _freed = cleanup_outputs_once(str(tmp_path), max_age_days=0, max_files=3)

        assert removed == 2
        assert (tmp_path / "image" / "img_3.png").exists()
        assert (tmp_path / "image" / "img_4.png").exists()
        assert not (tmp_path / "image" / "img_0.png").exists()

    def test_missing_dir_is_noop(self, tmp_path):
        removed, freed = cleanup_outputs_once(str(tmp_path / "nonexistent"), max_age_days=14, max_files=0)
        assert removed == 0
        assert freed == 0


class TestCleanupUploadsOnce:
    """data/uploads/ 留存策略清理（数据治理 P0-1）。

    验收标准：原始上传按 uploads_max_age_days 判龄；restored/ 成品子树与
    outputs 同策略单独判龄；.gitkeep 豁免；禁用时零操作；目录缺失零操作。
    """

    def test_disabled_when_both_rules_zero(self, tmp_path):
        _make_file(str(tmp_path / "image" / "a.jpg"))
        removed, _freed = cleanup_uploads_once(str(tmp_path), uploads_max_age_days=0, restored_max_age_days=0)
        assert removed == 0
        assert (tmp_path / "image" / "a.jpg").exists()

    def test_age_rule_removes_only_old_uploads(self, tmp_path):
        now = time.time()
        old_upload = tmp_path / "image" / "old.jpg"
        fresh_upload = tmp_path / "video" / "new.mp4"
        _make_file(str(old_upload), mtime=now - 10 * 86400)
        _make_file(str(fresh_upload), mtime=now)

        removed, freed = cleanup_uploads_once(str(tmp_path), uploads_max_age_days=7, restored_max_age_days=14)

        assert removed == 1
        assert freed == 128
        assert not old_upload.exists()
        assert fresh_upload.exists()

    def test_restored_subtree_uses_outputs_age(self, tmp_path):
        """restored/ 成品子树按 outputs 策略（14 天）判龄，而非 uploads 的 7 天。"""
        now = time.time()
        raw_expired = tmp_path / "image" / "raw_old.jpg"  # 10 天前，超 uploads 7 天 → 删
        restored_keep = tmp_path / "image" / "restored" / "mid.png"  # 10 天前，未超 outputs 14 天 → 留
        restored_expired = tmp_path / "restored" / "very_old.png"  # 20 天前，超 14 天 → 删
        _make_file(str(raw_expired), mtime=now - 10 * 86400)
        _make_file(str(restored_keep), mtime=now - 10 * 86400)
        _make_file(str(restored_expired), mtime=now - 20 * 86400)

        removed, _freed = cleanup_uploads_once(str(tmp_path), uploads_max_age_days=7, restored_max_age_days=14)

        assert removed == 2
        assert not raw_expired.exists()
        assert restored_keep.exists()
        assert not restored_expired.exists()

    def test_restored_subtree_untouched_when_restored_rule_disabled(self, tmp_path):
        """outputs 策略禁用（restored_max_age_days=0）时，restored/ 成品不被 uploads 规则误删。"""
        now = time.time()
        restored_old = tmp_path / "image" / "restored" / "old.png"
        _make_file(str(restored_old), mtime=now - 30 * 86400)

        removed, _freed = cleanup_uploads_once(str(tmp_path), uploads_max_age_days=7, restored_max_age_days=0)

        assert removed == 0
        assert restored_old.exists()

    def test_gitkeep_exempt(self, tmp_path):
        now = time.time()
        keep = tmp_path / "image" / ".gitkeep"
        _make_file(str(keep), mtime=now - 365 * 86400)

        cleanup_uploads_once(str(tmp_path), uploads_max_age_days=7, restored_max_age_days=14)

        assert keep.exists()

    def test_missing_dir_is_noop(self, tmp_path):
        removed, freed = cleanup_uploads_once(str(tmp_path / "nonexistent"), uploads_max_age_days=7)
        assert removed == 0
        assert freed == 0


class TestRetentionConfigUploads:
    """uploads_max_age_days 配置契约（数据治理 P0-1）。"""

    def test_default_is_shorter_than_outputs(self):
        from app.integrated_app.config_models import RetentionConfig

        cfg = RetentionConfig()
        # 原始上传比修复产物更隐私敏感：默认留存必须短于 outputs
        assert cfg.uploads_max_age_days == 7
        assert cfg.uploads_max_age_days < cfg.outputs_max_age_days

    def test_zero_disables_and_rejects_negative(self):
        from pydantic import ValidationError

        from app.integrated_app.config_models import RetentionConfig

        assert RetentionConfig(uploads_max_age_days=0).uploads_max_age_days == 0
        with pytest.raises(ValidationError):
            RetentionConfig(uploads_max_age_days=-1)


@pytest.mark.asyncio
class TestPruneOldRecords:
    """history.max_records 自动裁剪。"""

    async def _insert(self, db: HistoryDB, n: int, prefix: str = "img") -> None:
        for i in range(n):
            await db.add_record(
                HistoryRecord(
                    task_type="image",
                    input_file=f"{prefix}_{i}.png",
                    model_size="3b",
                    status="completed",
                    processing_time=1.0,
                )
            )

    async def test_prune_keeps_latest_records(self, tmp_path):
        # max_records=0 禁用自动裁剪，单独验证显式 prune 语义
        async with HistoryDB(db_path=str(tmp_path / "history.db"), max_records=0) as db:
            await self._insert(db, 8)

            deleted = await db.prune_old_records(max_records=5)

            assert deleted == 3
            assert await db.count_records() == 5
            # 保留的必须是最新的（id 最大的）记录
            records, _total = await db.get_records(limit=10)
            ids = [r.id for r in records]
            assert ids == sorted(ids, reverse=True)
            assert min(ids) == 4

    async def test_prune_noop_below_limit(self, tmp_path):
        async with HistoryDB(db_path=str(tmp_path / "history.db"), max_records=0) as db:
            await self._insert(db, 3)

            assert await db.prune_old_records(max_records=100) == 0
            assert await db.count_records() == 3

    async def test_prune_disabled_when_zero(self, tmp_path):
        async with HistoryDB(db_path=str(tmp_path / "history.db"), max_records=0) as db:
            await self._insert(db, 5)

            assert await db.prune_old_records() == 0
            assert await db.count_records() == 5

    async def test_prune_uses_constructor_limit_by_default(self, tmp_path):
        """不传参时使用构造时的 max_records 上限。"""
        async with HistoryDB(db_path=str(tmp_path / "history.db"), max_records=4) as db:
            await self._insert(db, 2)
            assert await db.prune_old_records() == 0
            await self._insert(db, 6)
            assert await db.count_records() == 4

    async def test_auto_prune_on_add_record(self, tmp_path):
        """写入路径自动落实上限：add_record 内部触发裁剪。"""
        async with HistoryDB(db_path=str(tmp_path / "history.db"), max_records=5) as db:
            await self._insert(db, 12)

            assert await db.count_records() <= 5

    async def test_prune_syncs_fts_index(self, tmp_path):
        async with HistoryDB(db_path=str(tmp_path / "history.db"), max_records=0) as db:
            await self._insert(db, 6, prefix="video")

            await db.prune_old_records(max_records=3)

            records, total = await db.search_records("video")
            assert total == 3
            assert len(records) == 3
            # 最旧的 3 条不应出现在 FTS 结果中
            inputs = {r.input_file for r in records}
            assert "video_0.png" not in inputs
            assert "video_5.png" in inputs


class TestEnsureDiskSpace:
    """任务启动前磁盘空间预检。"""

    def test_zero_threshold_skips_check(self, tmp_path):
        # 阈值 0 表示禁用预检，任何情况都放行
        ensure_disk_space(str(tmp_path), 0)

    def test_negative_threshold_skips_check(self, tmp_path):
        ensure_disk_space(str(tmp_path), -1.0)

    def test_unreachable_threshold_rejects_with_507(self, tmp_path):
        # P0-2 分层治理：服务层抛领域异常 DiskSpaceError（http_status=507），
        # 由全局异常处理器转换为 HTTP 响应
        with pytest.raises(DiskSpaceError) as exc_info:
            ensure_disk_space(str(tmp_path), 1e9)
        assert exc_info.value.http_status() == 507
        assert "磁盘剩余空间不足" in str(exc_info.value.message)

    def test_reasonable_threshold_passes(self, tmp_path):
        # 正常机器剩余空间远大于 1KB
        ensure_disk_space(str(tmp_path), 1e-6)

    def test_missing_dir_still_checks(self, tmp_path):
        # 目标目录不存在时以其父目录所在磁盘为检查对象（disk_usage 语义），
        # 极大阈值仍应拒绝
        with pytest.raises(DiskSpaceError):
            ensure_disk_space(str(tmp_path / "outputs"), 1e9)


@pytest.mark.asyncio
class TestCostVisibilityColumns:
    """每任务成本可见性（成本治理 P1-1）：输出体积列 + 聚合统计。"""

    async def test_output_size_persisted_and_aggregated(self, tmp_path):
        async with HistoryDB(db_path=str(tmp_path / "history.db")) as db:
            rid = await db.add_record(HistoryRecord(task_type="image", input_file="a.png", status="completed"))
            await db.update_record(rid, status="completed", output_size_bytes=2048, processing_time=12.5)
            await db.add_record(
                HistoryRecord(
                    task_type="video",
                    input_file="b.mp4",
                    status="completed",
                    output_size_bytes=4096,
                    processing_time=27.5,
                )
            )

            stats = await db.get_statistics()
            assert stats["total_output_bytes"] == 2048 + 4096
            assert abs(stats["total_processing_time"] - 40.0) < 0.01
            assert stats["total_records"] == 2

            record = await db.get_record(rid)
            assert record.output_size_bytes == 2048
            assert record.vram_peak_mb == 0.0

    async def test_migration_adds_columns_to_legacy_db(self, tmp_path):
        """老库（无新列）初始化时自动 ALTER TABLE 补列。"""
        db_path = str(tmp_path / "legacy.db")
        conn = sqlite3.connect(db_path)
        conn.execute("""CREATE TABLE history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_type TEXT NOT NULL,
                input_file TEXT NOT NULL,
                output_file TEXT DEFAULT '',
                model_size TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                parameters TEXT DEFAULT '{}',
                processing_time REAL DEFAULT 0.0,
                created_at TEXT NOT NULL,
                error_message TEXT DEFAULT ''
            )""")
        conn.commit()
        conn.close()

        async with HistoryDB(db_path=db_path) as db:
            rid = await db.add_record(HistoryRecord(task_type="image", input_file="x.png", status="completed"))
            await db.update_record(rid, output_size_bytes=512, vram_peak_mb=8123.5)
            record = await db.get_record(rid)
            assert record.output_size_bytes == 512
            assert abs(record.vram_peak_mb - 8123.5) < 0.01


@pytest.mark.asyncio
class TestStatisticsExcludeSoftDeleted:
    """统计排除软删除（回收站）记录：统计卡描述的是列表页可见记录集。

    回归背景：get_statistics 的五条聚合曾全部不带 deleted_at IS NULL，
    删除（默认进回收站）后 total_records 永不下降，UI 统计与列表不一致。
    """

    async def test_soft_deleted_excluded_then_restored_recounted(self, tmp_path):
        async with HistoryDB(db_path=str(tmp_path / "history.db")) as db:
            rid_a = await db.add_record(HistoryRecord(task_type="image", input_file="a.png", status="completed"))
            await db.update_record(rid_a, processing_time=10.0, output_size_bytes=1024)
            await db.add_record(HistoryRecord(task_type="image", input_file="b.png", status="failed"))

            stats = await db.get_statistics()
            assert stats["total_records"] == 2
            assert stats["by_status"]["completed"] == 1
            assert abs(stats["total_processing_time"] - 10.0) < 0.01

            # 软删除 completed 记录（默认 delete_record(soft=True)）→ 统计立即回落
            assert await db.delete_record(rid_a, soft=True) is True
            stats = await db.get_statistics()
            assert stats["total_records"] == 1
            assert stats["by_status"] == {"failed": 1}
            assert stats["by_type"] == {"image": 1}
            assert stats["avg_processing_time"] == 0
            assert stats["total_processing_time"] == 0
            assert stats["total_output_bytes"] == 0

            # 从回收站恢复 → 重新计入
            assert await db.restore_records([rid_a]) == 1
            stats = await db.get_statistics()
            assert stats["total_records"] == 2
            assert abs(stats["total_processing_time"] - 10.0) < 0.01
            assert stats["total_output_bytes"] == 1024
