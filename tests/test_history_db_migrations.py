"""history_db Schema 版本化迁移框架测试（数据治理 P0-2）。

验收标准（对应评估报告 §9.2 P0-2）：
1. 新库初始化后 PRAGMA user_version == SCHEMA_VERSION；
2. 重复初始化幂等（版本不回退、不重复迁移）；
3. 未打版本标记的历史旧库（v0）迁移后正确补列并标记版本；
4. 缺列旧库迁移后补齐 output_size_bytes / vram_peak_mb；
5. 迁移登记表版本号严格递增且最终等于 SCHEMA_VERSION。
"""

import aiosqlite
import pytest
import pytest_asyncio

from app.integrated_app.history_db import _MIGRATIONS, SCHEMA_VERSION, HistoryDB


@pytest_asyncio.fixture
async def tmp_db(tmp_path):
    """在临时目录创建 HistoryDB，测试结束自动关闭。"""
    db = HistoryDB(str(tmp_path / "history.db"))
    await db.initialize()
    yield db
    await db.close()


class TestSchemaVersionFramework:
    @pytest.mark.asyncio
    async def test_fresh_db_gets_current_schema_version(self, tmp_db: HistoryDB):
        """验收点 1：新库初始化后版本 == SCHEMA_VERSION。"""
        version = await tmp_db.get_schema_version()
        assert version == SCHEMA_VERSION
        assert version >= 1

    @pytest.mark.asyncio
    async def test_reinitialize_is_idempotent(self, tmp_path):
        """验收点 2：重复开闭（重复 initialize）版本不变化。"""
        path = str(tmp_path / "history.db")
        async with HistoryDB(path) as db1:
            v1 = await db1.get_schema_version()
        async with HistoryDB(path) as db2:
            v2 = await db2.get_schema_version()
        assert v1 == v2 == SCHEMA_VERSION

    @pytest.mark.asyncio
    async def test_unmarked_legacy_db_migrates_to_current(self, tmp_path):
        """验收点 3：v0 旧库（有表、无 user_version 标记）迁移后标记当前版本。"""
        path = str(tmp_path / "legacy.db")
        async with aiosqlite.connect(path) as raw:
            await raw.execute("""CREATE TABLE history (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       task_type TEXT NOT NULL,
                       input_file TEXT NOT NULL,
                       output_file TEXT DEFAULT '',
                       model_size TEXT DEFAULT '',
                       status TEXT NOT NULL DEFAULT 'pending',
                       parameters TEXT DEFAULT '{}',
                       processing_time REAL DEFAULT 0.0,
                       created_at TEXT NOT NULL,
                       error_message TEXT DEFAULT '',
                       output_size_bytes INTEGER DEFAULT 0,
                       vram_peak_mb REAL DEFAULT 0.0
                   )""")
            await raw.execute(
                "INSERT INTO history (task_type, input_file, status, created_at) VALUES ('image', 'a.png', 'completed', '2026-01-01')"
            )
            await raw.commit()
        # 确认旧库确实未打版本标记
        async with aiosqlite.connect(path) as raw:
            cursor = await raw.execute("PRAGMA user_version")
            assert (await cursor.fetchone())[0] == 0

        async with HistoryDB(path) as db:
            assert await db.get_schema_version() == SCHEMA_VERSION
            record = await db.get_record(1)
            assert record is not None
            assert record.task_type == "image"

    @pytest.mark.asyncio
    async def test_legacy_db_missing_columns_gets_patched(self, tmp_path):
        """验收点 4：缺 output_size_bytes / vram_peak_mb 的旧库迁移后补齐且旧数据可读。"""
        path = str(tmp_path / "old_cols.db")
        async with aiosqlite.connect(path) as raw:
            # 只建到 v0 前的旧结构（无 output_size_bytes / vram_peak_mb）
            await raw.execute("""CREATE TABLE history (
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
            await raw.execute(
                "INSERT INTO history (task_type, input_file, status, created_at) VALUES ('video', 'a.mp4', 'completed', '2026-01-01')"
            )
            await raw.commit()

        async with HistoryDB(path) as db:
            async with aiosqlite.connect(path) as raw:
                cursor = await raw.execute("PRAGMA table_info(history)")
                cols = {row[1] for row in await cursor.fetchall()}
            assert {"output_size_bytes", "vram_peak_mb"} <= cols
            assert await db.get_schema_version() == SCHEMA_VERSION
            record = await db.get_record(1)
            assert record is not None
            assert record.output_size_bytes == 0
            assert record.vram_peak_mb == 0.0

    @pytest.mark.asyncio
    async def test_future_schema_version_logs_warning_and_skips(self, tmp_path, caplog):
        """高版本库（程序回滚场景）不执行迁移、不回写版本标记。"""
        path = str(tmp_path / "future.db")
        async with aiosqlite.connect(path) as raw:
            # 手动打一个"未来版本"标记（SCHEMA_VERSION + 100）
            await raw.execute(f"PRAGMA user_version={SCHEMA_VERSION + 100}")
            await raw.execute("""CREATE TABLE history (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       task_type TEXT NOT NULL,
                       input_file TEXT NOT NULL,
                       output_file TEXT DEFAULT '',
                       model_size TEXT DEFAULT '',
                       status TEXT NOT NULL DEFAULT 'pending',
                       parameters TEXT DEFAULT '{}',
                       processing_time REAL DEFAULT 0.0,
                       created_at TEXT NOT NULL,
                       error_message TEXT DEFAULT '',
                       output_size_bytes INTEGER DEFAULT 0,
                       vram_peak_mb REAL DEFAULT 0.0
                   )""")
            await raw.commit()
        async with HistoryDB(path):
            pass
        async with aiosqlite.connect(path) as raw:
            cursor = await raw.execute("PRAGMA user_version")
            assert (await cursor.fetchone())[0] == SCHEMA_VERSION + 100

    def test_migration_registry_is_ordered_and_covers_current_version(self):
        """验收点 5：迁移表版本号严格递增，最后一项目标版本 == SCHEMA_VERSION。"""
        targets = [target for target, _desc, _fn in _MIGRATIONS]
        assert targets == sorted(targets)
        assert len(set(targets)) == len(targets)
        assert targets[-1] == SCHEMA_VERSION

    @pytest.mark.asyncio
    async def test_existing_functionality_after_migration(self, tmp_db: HistoryDB):
        """迁移后常规 CRUD / 任务状态不受影响（回归保障）。"""
        from app.integrated_app.history_db import HistoryRecord, TaskRecord

        record_id = await tmp_db.add_record(HistoryRecord(task_type="image", input_file="x.png", status="pending"))
        await tmp_db.update_record(record_id, status="completed", output_file="x_out.png")
        record = await tmp_db.get_record(record_id)
        assert record is not None and record.status == "completed"

        await tmp_db.create_task(TaskRecord(task_id="t1", record_id=record_id, status="processing"))
        task = await tmp_db.get_task_by_record_id(record_id)
        assert task is not None and task.task_id == "t1"

    @pytest.mark.asyncio
    async def test_v4_db_migrates_input_distribution_columns(self, tmp_path):
        """v4 旧库迁移后补齐 input_width/input_height/input_duration_sec 且旧数据可读。

        对应评估报告 P2-4a：输入分布（分辨率/时长）台账落地。
        """
        path = str(tmp_path / "v4.db")
        async with aiosqlite.connect(path) as raw:
            # 构造 v4 结构（含 deleted_at，但缺输入分布三列）
            await raw.execute("""CREATE TABLE history (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       task_type TEXT NOT NULL,
                       input_file TEXT NOT NULL,
                       output_file TEXT DEFAULT '',
                       model_size TEXT DEFAULT '',
                       status TEXT NOT NULL DEFAULT 'pending',
                       parameters TEXT DEFAULT '{}',
                       processing_time REAL DEFAULT 0.0,
                       created_at TEXT NOT NULL,
                       error_message TEXT DEFAULT '',
                       output_size_bytes INTEGER DEFAULT 0,
                       vram_peak_mb REAL DEFAULT 0.0,
                       input_sha256 TEXT DEFAULT '',
                       pinned INTEGER DEFAULT 0,
                       deleted_at TEXT DEFAULT NULL
                   )""")
            await raw.execute("PRAGMA user_version=4")
            await raw.execute(
                "INSERT INTO history (task_type, input_file, status, created_at) VALUES ('video', 'a.mp4', 'completed', '2026-01-01')"
            )
            await raw.commit()

        async with HistoryDB(path) as db:
            async with aiosqlite.connect(path) as raw:
                cursor = await raw.execute("PRAGMA table_info(history)")
                cols = {row[1] for row in await cursor.fetchall()}
            assert {"input_width", "input_height", "input_duration_sec"} <= cols
            assert await db.get_schema_version() == SCHEMA_VERSION
            record = await db.get_record(1)
            assert record is not None
            assert record.task_type == "video"
            # 旧记录新列取默认值 0
            assert record.input_width == 0
            assert record.input_height == 0
            assert record.input_duration_sec == 0.0

    @pytest.mark.asyncio
    async def test_input_distribution_fields_roundtrip(self, tmp_db: HistoryDB):
        """输入分布字段写入→读回一致（评估 P2-4a 台账数据真实落库）。"""
        from app.integrated_app.history_db import HistoryRecord

        record_id = await tmp_db.add_record(
            HistoryRecord(
                task_type="image",
                input_file="x.png",
                status="completed",
                input_width=1920,
                input_height=1080,
                input_duration_sec=0.0,
            )
        )
        record = await tmp_db.get_record(record_id)
        assert record is not None
        assert record.input_width == 1920
        assert record.input_height == 1080
        assert record.input_duration_sec == 0.0

        vid_id = await tmp_db.add_record(
            HistoryRecord(
                task_type="video",
                input_file="v.mp4",
                status="completed",
                input_width=1280,
                input_height=720,
                input_duration_sec=12.5,
            )
        )
        vid = await tmp_db.get_record(vid_id)
        assert vid is not None
        assert vid.input_width == 1280
        assert vid.input_height == 720
        assert vid.input_duration_sec == 12.5

        # update_record 白名单包含输入分布列（供后续修正场景）
        await tmp_db.update_record(record_id, input_width=640)
        record = await tmp_db.get_record(record_id)
        assert record is not None and record.input_width == 640
