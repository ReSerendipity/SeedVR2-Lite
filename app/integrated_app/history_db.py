# SPDX-FileCopyrightText: Copyright (c) 2024-2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""SQLite 历史记录与任务状态持久化模块

使用 aiosqlite 提供异步 SQLite 数据库访问，管理两类数据:
1. HistoryRecord: 修复任务历史记录（视频/图像），支持全文搜索（FTS5）
2. TaskRecord: 后台运行任务的实时状态持久化，用于崩溃恢复

数据库特性:
- WAL 模式: 启用 Write-Ahead Logging 提升并发读写性能
- FTS5 全文搜索: 对输入/输出文件名、模型大小、状态建立全文索引
- 自动触发器: INSERT/UPDATE/DELETE 时自动同步 FTS 索引
- Schema 版本化迁移: 通过 ``PRAGMA user_version`` 标记结构版本（数据治理 P0-2），
  增量结构变更登记在 ``_MIGRATIONS`` 迁移表中按序执行，每步迁移必须幂等
- 异步上下文管理器: 支持 async with 语法，确保连接正确释放
- 白名单列验证: UPDATE 操作验证列名，防止 SQL 注入
- 批量插入降级: 批量插入失败时自动回退到逐条插入，保证鲁棒性
"""

from __future__ import annotations

import logging
import os
import sqlite3
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

import aiosqlite

from .utils.fts import escape_fts_query

logger = logging.getLogger(__name__)

# 历史库 schema 当前版本（数据治理 P0-2）。
# 约定：新增列/索引等结构变更时 +1，并在 _MIGRATIONS 登记对应迁移步骤（v0 表示
# 未打版本标记的历史旧库）。首次建表即包含全部列，因此新库从 v0 一步推进到最新版。
SCHEMA_VERSION = 5


async def _migrate_v2_to_v3(db: aiosqlite.Connection) -> None:
    """v2 → v3：history 表新增 pinned 列（数据治理 P1-5 用户「标记保留」）。

    pinned=1 的记录，其输出文件被 retention 年龄/数量清理豁免。
    必须幂等：列已存在时 no-op。

    Args:
        db: aiosqlite 连接。
    """
    cursor = await db.execute("PRAGMA table_info(history)")
    existing_cols = {row[1] for row in await cursor.fetchall()}
    if existing_cols and "pinned" not in existing_cols:
        await db.execute("ALTER TABLE history ADD COLUMN pinned INTEGER DEFAULT 0")


async def _migrate_v1_to_v2(db: aiosqlite.Connection) -> None:
    """v1 → v2：history 表新增 input_sha256 列（数据治理 P1-1 内容寻址血缘）。

    必须幂等：列已存在时 no-op。

    Args:
        db: aiosqlite 连接。
    """
    cursor = await db.execute("PRAGMA table_info(history)")
    existing_cols = {row[1] for row in await cursor.fetchall()}
    if existing_cols and "input_sha256" not in existing_cols:
        await db.execute("ALTER TABLE history ADD COLUMN input_sha256 TEXT DEFAULT ''")


async def _migrate_v3_to_v4(db: aiosqlite.Connection) -> None:
    """v3 → v4：history 表新增 deleted_at 列（回收站 / 防误删）。

    必须幂等：列已存在时 no-op。
    """
    cursor = await db.execute("PRAGMA table_info(history)")
    existing_cols = {row[1] for row in await cursor.fetchall()}
    if existing_cols and "deleted_at" not in existing_cols:
        await db.execute("ALTER TABLE history ADD COLUMN deleted_at TEXT DEFAULT NULL")


async def _migrate_v4_to_v5(db: aiosqlite.Connection) -> None:
    """v4 → v5：history 表新增输入分布列（输入分辨率/时长，评估 P2-4a 漂移台账）。

    ``input_width`` / ``input_height`` 为输入媒体像素宽高（图片与视频均适用），
    ``input_duration_sec`` 为视频时长（秒），图片恒为 0。缺探测数据时保持 0
    （fail-open，不阻塞任务主流程）。必须幂等：列已存在时 no-op。
    """
    cursor = await db.execute("PRAGMA table_info(history)")
    existing_cols = {row[1] for row in await cursor.fetchall()}
    if existing_cols and "input_width" not in existing_cols:
        await db.execute("ALTER TABLE history ADD COLUMN input_width INTEGER DEFAULT 0")
    if existing_cols and "input_height" not in existing_cols:
        await db.execute("ALTER TABLE history ADD COLUMN input_height INTEGER DEFAULT 0")
    if existing_cols and "input_duration_sec" not in existing_cols:
        await db.execute("ALTER TABLE history ADD COLUMN input_duration_sec REAL DEFAULT 0.0")


async def _migrate_v0_to_v1(db: aiosqlite.Connection) -> None:
    """v0（未打版本标记的旧库）→ v1：补列 output_size_bytes / vram_peak_mb。

    兼容在本迁移框架引入之前创建的历史库（成本治理 P1-1 存储可见性 +
    P2-1 VRAM 峰值落库两批增量列）。必须幂等：新库 CREATE TABLE 已含全部列，
    重复执行为 no-op。

    Args:
        db: aiosqlite 连接。
    """
    cursor = await db.execute("PRAGMA table_info(history)")
    existing_cols = {row[1] for row in await cursor.fetchall()}
    if existing_cols and "output_size_bytes" not in existing_cols:
        await db.execute("ALTER TABLE history ADD COLUMN output_size_bytes INTEGER DEFAULT 0")
    if existing_cols and "vram_peak_mb" not in existing_cols:
        await db.execute("ALTER TABLE history ADD COLUMN vram_peak_mb REAL DEFAULT 0.0")


# 迁移登记表：(目标版本, 描述, 迁移函数)。按目标版本升序排列，逐版本顺序执行。
# 新增迁移时：SCHEMA_VERSION += 1，并在此追加一项；迁移函数必须幂等。
_MIGRATIONS: tuple[tuple[int, str, Callable[[aiosqlite.Connection], Awaitable[None]]], ...] = (
    (1, "补列 output_size_bytes / vram_peak_mb（旧库兼容）", _migrate_v0_to_v1),
    (2, "补列 input_sha256（源文件内容寻址血缘，P1-1）", _migrate_v1_to_v2),
    (3, "补列 pinned（用户标记保留，retention 清理豁免，数据治理 P1-5）", _migrate_v2_to_v3),
    (4, "补列 deleted_at（软删除 / 回收站，防误删）", _migrate_v3_to_v4),
    (5, "补列 input_width / input_height / input_duration_sec（输入分布台账，评估 P2-4a）", _migrate_v4_to_v5),
)


@dataclass
class HistoryRecord:
    """修复任务历史记录数据类。

    存储单次视频/图像修复任务的完整元信息，用于历史页面展示和统计。

    Attributes:
        id: 数据库自增主键，None 表示新记录尚未插入。
        task_type: 任务类型，"video" 或 "image"。
        input_file: 输入文件路径。
        output_file: 输出文件路径。
        model_size: 使用的模型大小/版本标识。
        status: 任务状态: pending / processing / completed / failed / cancelled。
        parameters: 任务参数 JSON 字符串。
        processing_time: 处理耗时（秒）。
        created_at: 记录创建时间（ISO 格式字符串）。
        error_message: 失败时的错误信息。
        output_size_bytes: 输出文件大小（字节），用于按任务聚合存储成本（P1-1）。
        vram_peak_mb: 本次推理的 VRAM 峰值（MB），无监控数据时为 0（P2-1）。
        input_sha256: 源输入文件内容 SHA-256（hex），内容寻址血缘（数据治理 P1-1）；
            空串表示未计算（如内存数据库/测试桩场景）。
        input_width: 输入媒体像素宽（探测失败时为 0；评估 P2-4a 输入分布台账）。
        input_height: 输入媒体像素高（探测失败时为 0）。
        input_duration_sec: 输入视频时长（秒）；图片恒为 0（评估 P2-4a）。
        pinned: 用户「标记保留」。置位后该记录的输出文件被 retention
            年龄/数量清理豁免（数据治理 P1-5）。
    """

    id: int | None = None
    task_type: str = ""
    input_file: str = ""
    output_file: str = ""
    model_size: str = ""
    status: str = ""
    parameters: str = ""
    processing_time: float = 0.0
    created_at: str = ""
    error_message: str = ""
    output_size_bytes: int = 0
    vram_peak_mb: float = 0.0
    input_sha256: str = ""
    input_width: int = 0
    input_height: int = 0
    input_duration_sec: float = 0.0
    pinned: bool = False


@dataclass
class TaskRecord:
    """后台任务实时状态记录数据类。

    持久化运行中任务的状态，用于应用重启后恢复未完成任务。

    Attributes:
        task_id: 任务唯一标识符（UUID）。
        record_id: 关联的 HistoryRecord 主键 ID。
        status: 任务状态: pending / processing / completed / failed / cancelled。
        progress: 任务进度（0.0 ~ 100.0）。
        output_path: 输出文件路径（任务完成后填充）。
        error_message: 失败时的错误信息。
        updated_at: 最后更新时间（ISO 格式字符串）。
    """

    task_id: str = ""
    record_id: int = 0
    status: str = ""
    progress: float = 0.0
    output_path: str = ""
    error_message: str = ""
    updated_at: str = ""


class HistoryDB:
    """历史记录与任务状态异步数据库管理器。

    提供历史记录 CRUD、全文搜索、统计，以及任务状态持久化接口。
    支持异步上下文管理器协议（async with），确保异常路径下数据库连接也能正确释放。

    Attributes:
        db_path: SQLite 数据库文件路径。
        _initialized: 数据库是否已初始化（表结构已创建）。
        _db: aiosqlite 持久连接对象，None 表示未连接。
    """

    def __init__(self, db_path: str = "data/history.db", timeout: float = 30.0, max_records: int = 10000):
        """初始化历史数据库管理器。

        Args:
            db_path: SQLite 数据库文件路径，默认 "data/history.db"。
                路径所在目录不存在时会在 initialize() 中自动创建。
            timeout: 获取数据库锁的最长等待时间（秒），默认 30.0。
                传递给底层 sqlite3.connect，避免高并发写入时因锁竞争
                立即抛出 "database is locked"，而是在超时窗口内重试等待。
            max_records: 历史记录保留上限，超出时自动裁剪最旧记录（落实
                config.yaml history.max_records 的"超出自动清理"承诺），0 表示不限制。
        """
        self.db_path = db_path
        self.timeout = timeout
        self.max_records = max_records
        self._initialized = False
        self._db: aiosqlite.Connection | None = None

    # REFACTOR: 支持异步上下文管理器协议，确保异常路径下连接也能被释放 (E7)
    async def __aenter__(self) -> HistoryDB:
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def initialize(self):
        """初始化数据库，创建表结构"""
        if self._initialized:
            return

        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

        self._db = await aiosqlite.connect(self.db_path, timeout=self.timeout)
        db = self._db

        # 启用 WAL 模式以提升并发读写性能
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")
        # 与连接 timeout 对齐：锁竞争时在超时窗口内忙等重试（毫秒）
        # nosemgrep: python.lang.security.audit.formatted-sql-query.formatted-sql-query, python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query - int 转型的配置常量（timeout 恒为默认值），无注入面
        await db.execute(f"PRAGMA busy_timeout={int(self.timeout * 1000)}")

        # 数据治理 P2-4：建表前先探测是否为已存在数据的旧库——
        # CREATE TABLE IF NOT EXISTS 之后探测会把全新空库误判为旧库
        cursor_probe = await db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='history'")
        history_table_existed = await cursor_probe.fetchone() is not None

        await db.execute("""
            CREATE TABLE IF NOT EXISTS history (
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
            )
        """)

        # ---- 版本化迁移（数据治理 P0-2）：按 _MIGRATIONS 顺序推进 user_version ----
        current_version = await self._get_schema_version(db)
        if current_version > SCHEMA_VERSION:
            logger.warning(
                f"历史数据库 schema 版本 ({current_version}) 高于代码版本 ({SCHEMA_VERSION})，"
                f"可能是程序回滚，跳过迁移"
            )
        else:
            # 数据治理 P2-4：升级前自动备份（空库/同版本不备份，失败不阻断迁移）
            await self._backup_before_migration(db, current_version, history_table_existed)
            initial_version = current_version
            for target, desc, migrate in _MIGRATIONS:
                if current_version < target:
                    logger.info(f"应用 history schema 迁移 v{current_version} → v{target}: {desc}")
                    await migrate(db)
                    current_version = target
            # 版本落盘：迁移函数只负责结构变更，user_version 标记统一在此收口。
            # 判据用"落库前版本"而非循环推进后的变量——新库从 v0 一步推进到最新版
            # 时推进后变量已等于 SCHEMA_VERSION，旧写法（<）与同值比较均会漏写。
            if initial_version != SCHEMA_VERSION:
                await self._set_schema_version(db, SCHEMA_VERSION)
                logger.info(f"历史数据库 schema 版本已标记为 v{SCHEMA_VERSION}")

        await db.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                record_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                progress REAL DEFAULT 0.0,
                output_path TEXT DEFAULT '',
                error_message TEXT DEFAULT '',
                updated_at TEXT NOT NULL
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_tasks_record_id ON tasks(record_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")

        # 创建全文搜索虚拟表
        await db.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS history_fts
            USING fts5(id, input_file, output_file, model_size, status, content=history, content_rowid=id)
        """)

        # 创建触发器保持 FTS 索引同步
        await db.execute("""
            CREATE TRIGGER IF NOT EXISTS history_ai AFTER INSERT ON history BEGIN
                INSERT INTO history_fts(rowid, id, input_file, output_file, model_size, status)
                VALUES (new.id, new.id, new.input_file, new.output_file, new.model_size, new.status);
            END
        """)

        await db.execute("""
            CREATE TRIGGER IF NOT EXISTS history_ad AFTER DELETE ON history BEGIN
                INSERT INTO history_fts(history_fts, rowid, id, input_file, output_file, model_size, status)
                VALUES ('delete', old.id, old.id, old.input_file, old.output_file, old.model_size, old.status);
            END
        """)

        await db.execute("""
            CREATE TRIGGER IF NOT EXISTS history_au AFTER UPDATE ON history BEGIN
                INSERT INTO history_fts(history_fts, rowid, id, input_file, output_file, model_size, status)
                VALUES ('delete', old.id, old.id, old.input_file, old.output_file, old.model_size, old.status);
                INSERT INTO history_fts(rowid, id, input_file, output_file, model_size, status)
                VALUES (new.id, new.id, new.input_file, new.output_file, new.model_size, new.status);
            END
        """)

        # 创建索引
        await db.execute("CREATE INDEX IF NOT EXISTS idx_history_type ON history(task_type)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_history_status ON history(status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_history_created ON history(created_at)")

        await db.commit()

        self._initialized = True
        logger.info(f"历史数据库已初始化: {self.db_path}")

    # ==================== 私有辅助方法 ====================

    async def _backup_before_migration(
        self, db: aiosqlite.Connection, current_version: int, history_table_existed: bool
    ) -> None:
        """结构升级前把当前库快照到 ``{db_path}.bak-v{current_version}``（数据治理 P2-4）。

        仅当调用方确认建表前已存在 history 表（真实旧库，而非首次启动的空库）
        且版本低于代码版本时执行；``VACUUM INTO`` 生成含 WAL 已提交内容的
        一致性快照。目标文件已存在时跳过（保留最早一次该版本的备份，避免
        覆盖）；备份失败只告警不阻断迁移（迁移函数本身幂等）。

        Args:
            db: aiosqlite 连接。
            current_version: 迁移前的 schema 版本（0 表示未打标记的旧库）。
            history_table_existed: 建表前是否已存在 history 表。
        """
        if current_version >= SCHEMA_VERSION:
            return
        if not history_table_existed:
            return  # 全新空库，无需备份
        backup_path = f"{self.db_path}.bak-v{current_version}"
        if os.path.exists(backup_path):
            logger.info(f"迁移前备份已存在，跳过: {backup_path}")
            return
        try:
            await db.execute("VACUUM INTO ?", (backup_path,))
            logger.info(f"迁移前自动备份完成: {backup_path}")
        except Exception as e:  # noqa: BLE001 — 备份失败不阻断迁移主流程
            logger.warning(f"迁移前自动备份失败（继续迁移）: {e}")

    async def _get_schema_version(self, db: aiosqlite.Connection) -> int:
        """读取数据库 schema 版本标记（PRAGMA user_version），无标记返回 0。

        Args:
            db: aiosqlite 连接。

        Returns:
            当前 schema 版本号；未打过标记的历史旧库为 0。
        """
        cursor = await db.execute("PRAGMA user_version")
        row = await cursor.fetchone()
        return int(row[0]) if row else 0

    async def _set_schema_version(self, db: aiosqlite.Connection, version: int) -> None:
        """写入 schema 版本标记（PRAGMA user_version）。version 为代码内常量，无注入面。

        Args:
            db: aiosqlite 连接。
            version: 要写入的版本号（来自 SCHEMA_VERSION 常量）。
        """
        # nosemgrep: python.lang.security.audit.formatted-sql-query.formatted-sql-query, python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query - version 为代码常量 SCHEMA_VERSION，无注入面
        await db.execute(f"PRAGMA user_version={int(version)}")

    async def get_schema_version(self) -> int:
        """查询当前数据库的 schema 版本（公开接口，供运维/测试自检）。

        Returns:
            当前 schema 版本号；未初始化时返回 0。
        """
        if not self._initialized or self._db is None:
            return 0
        return await self._get_schema_version(self._db)

    async def _execute_write(
        self,
        sql: str,
        params: tuple | list = (),
        *,
        many: bool = False,
        want_rowcount: bool = False,
    ) -> int:
        """统一写入入口（INSERT/UPDATE/DELETE），自动提交。
        使用持久连接；若未初始化则先初始化。

        Args:
            sql: SQL 语句
            params: 单条参数 tuple 或批量参数 list[tuple]（需配合 many=True）
            many: True 时使用 executemany 批量执行
            want_rowcount: True 时返回受影响行数（DELETE/UPDATE 计数用）。
                False（默认）时返回 lastrowid（INSERT 主键用）。注意部分
                sqlite3 版本在 DELETE 后 lastrowid 仍非 None（残留上次
                INSERT 的 rowid），因此需要行数时必须显式传 True。

        Returns:
            want_rowcount=True 时为 rowcount；否则为 lastrowid（INSERT）或 rowcount。
        """
        if not self._initialized:
            await self.initialize()
        assert self._db is not None
        if many:
            cursor = await self._db.executemany(sql, params)
        else:
            cursor = await self._db.execute(sql, params)
        await self._db.commit()
        if want_rowcount:
            return cursor.rowcount if cursor.rowcount is not None else 0
        return cursor.lastrowid if cursor.lastrowid is not None else cursor.rowcount

    async def _fetch_one(self, sql: str, params: tuple | list = ()) -> sqlite3.Row | None:
        """执行查询并返回单行结果，行以 sqlite3.Row 形式返回。
        使用持久连接；若未初始化则先初始化。"""
        if not self._initialized:
            await self.initialize()
        assert self._db is not None
        self._db.row_factory = sqlite3.Row
        cursor = await self._db.execute(sql, params)
        return await cursor.fetchone()

    async def _fetch_all(self, sql: str, params: tuple | list = ()) -> list[sqlite3.Row]:
        """执行查询并返回所有结果行，行以 sqlite3.Row 形式返回。
        使用持久连接；若未初始化则先初始化。"""
        if not self._initialized:
            await self.initialize()
        assert self._db is not None
        self._db.row_factory = sqlite3.Row
        cursor = await self._db.execute(sql, params)
        return list(await cursor.fetchall())

    # ==================== 历史记录管理 ====================

    async def add_record(self, record: HistoryRecord) -> int:
        """添加历史记录，返回记录 ID"""
        if not record.created_at:
            record.created_at = datetime.now().isoformat()

        record_id = await self._execute_write(
            """INSERT INTO history (task_type, input_file, output_file, model_size, status, parameters, processing_time, created_at, error_message, output_size_bytes, vram_peak_mb, input_sha256, input_width, input_height, input_duration_sec)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.task_type,
                record.input_file,
                record.output_file,
                record.model_size,
                record.status,
                record.parameters,
                record.processing_time,
                record.created_at,
                record.error_message,
                record.output_size_bytes,
                record.vram_peak_mb,
                record.input_sha256,
                record.input_width,
                record.input_height,
                record.input_duration_sec,
            ),
        )
        await self._maybe_prune()
        return record_id

    async def add_records(self, records: list[HistoryRecord]) -> list[int]:
        """批量添加历史记录，通过 _execute_write 统一入口写入，失败回退到逐条插入"""
        if not records:
            return []

        now = datetime.now().isoformat()
        rows = []
        for record in records:
            if not record.created_at:
                record.created_at = now
            rows.append(
                (
                    record.task_type,
                    record.input_file,
                    record.output_file,
                    record.model_size,
                    record.status,
                    record.parameters,
                    record.processing_time,
                    record.created_at,
                    record.error_message,
                    record.output_size_bytes,
                    record.vram_peak_mb,
                    record.input_sha256,
                    record.input_width,
                    record.input_height,
                    record.input_duration_sec,
                )
            )

        sql = """INSERT INTO history (task_type, input_file, output_file, model_size, status, parameters, processing_time, created_at, error_message, output_size_bytes, vram_peak_mb, input_sha256, input_width, input_height, input_duration_sec)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""

        try:
            # P1-7：先取当前最大 id 作为基线，插入后按基线推算整批 id。
            # 旧实现依赖 last_insert_rowid() 反推，语义脆弱（DELETE 后 rowcount 残留等）；
            # 本类为单连接串行写，MAX(id) 基线在单事务内是确定性的。
            base_row = await self._fetch_one("SELECT COALESCE(MAX(id), 0) FROM history")
            base_id = int(base_row[0]) if base_row else 0
            await self._execute_write(sql, rows, many=True)
            await self._maybe_prune()
            return list(range(base_id + 1, base_id + len(rows) + 1))
        except (aiosqlite.Error, sqlite3.Error, OSError) as e:
            # ROBUSTNESS: 批量插入失败时降级为逐条插入；仅捕获 DB/IO 异常，
            # 不吞掉 KeyboardInterrupt 等系统级异常 (E2)。逐条路径无法可靠
            # 还原每行 id（调用方不消费该返回值），返回空列表。
            logger.warning(f"批量插入失败，回退到逐条插入: {type(e).__name__}: {e}")
            inserted = 0
            for row in rows:
                try:
                    await self._execute_write(sql, row)
                    inserted += 1
                except (aiosqlite.Error, sqlite3.Error, OSError) as row_err:
                    logger.error(f"单条插入失败，跳过: {row_err}")
                    continue
            if inserted:
                await self._maybe_prune()
            return []

    async def update_record(self, record_id: int, **kwargs) -> bool:
        """更新历史记录"""
        if not kwargs:
            return False

        # 列名白名单验证，防止 SQL 注入；SET 子句中的列名来自白名单，不存在注入风险
        allowed_columns = {
            "status",
            "output_file",
            "processing_time",
            "error_message",
            "parameters",
            "output_size_bytes",
            "vram_peak_mb",
            "input_sha256",
            "input_width",
            "input_height",
            "input_duration_sec",
            "pinned",
        }
        invalid_keys = set(kwargs.keys()) - allowed_columns
        if invalid_keys:
            raise ValueError(f"不允许更新的列: {invalid_keys}，允许的列: {allowed_columns}")

        set_clause = ", ".join(f"{k} = ?" for k in kwargs)
        values = list(kwargs.values()) + [record_id]

        await self._execute_write(f"UPDATE history SET {set_clause} WHERE id = ?", values)
        return True

    async def get_record(self, record_id: int) -> HistoryRecord | None:
        """获取单条记录"""
        row = await self._fetch_one("SELECT * FROM history WHERE id = ?", (record_id,))
        if row:
            return self._row_to_record(row)
        return None

    # OPTIMIZE: 批量查询接口，修复 recover_tasks 中的 N+1 查询 (C3)
    # 原实现循环调用 get_record(record_id)，N 条任务产生 N 次 DB 查询
    async def get_records_by_ids(self, record_ids: Sequence[int]) -> list[HistoryRecord]:
        """根据多个 ID 批量查询历史记录。

        Args:
            record_ids: 历史记录 ID 序列（允许重复与空）

        Returns:
            命中的 HistoryRecord 列表（顺序以数据库返回为准，不保证与输入顺序一致）。
            空输入返回空列表，避免在 SQL 中构造空 IN() 子句。
        """
        ids = [int(rid) for rid in record_ids if rid is not None]
        if not ids:
            return []
        placeholders = ", ".join("?" for _ in ids)
        rows = await self._fetch_all(
            f"SELECT * FROM history WHERE id IN ({placeholders})",
            tuple(ids),
        )
        return [self._row_to_record(row) for row in rows]

    async def get_records(
        self,
        task_type: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
        order_by: str = "created_at",
        order_dir: str = "DESC",
    ) -> tuple[list[HistoryRecord], int]:
        """获取记录列表（分页）"""
        conditions = []
        params: list = []

        if task_type:
            conditions.append("task_type = ?")
            params.append(task_type)
        if status:
            conditions.append("status = ?")
            params.append(status)
        # 回收站隔离：正常列表永远排除已软删除的记录
        conditions.append("deleted_at IS NULL")

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        # 获取分页数据
        valid_orders = {"created_at", "id", "task_type", "status", "processing_time"}
        if order_by not in valid_orders:
            order_by = "created_at"
        if order_dir not in ("ASC", "DESC"):
            order_dir = "DESC"

        # 获取总数
        count_row = await self._fetch_one(f"SELECT COUNT(*) FROM history WHERE {where_clause}", params)
        total = count_row[0] if count_row else 0

        rows = await self._fetch_all(
            f"SELECT * FROM history WHERE {where_clause} ORDER BY {order_by} {order_dir} LIMIT ? OFFSET ?",
            params + [limit, offset],
        )
        records = [self._row_to_record(row) for row in rows]
        return records, total

    async def search_records(self, query: str, limit: int = 50, offset: int = 0) -> tuple[list[HistoryRecord], int]:
        """全文搜索记录"""
        if not query.strip():
            return await self.get_records(limit=limit, offset=offset)

        # SECURITY: FTS5 查询转义，防止用户输入注入 FTS5 操作符（*, OR, AND, NOT, "", (), :, ^, -）
        # 原实现直接把 query 传给 MATCH，可被构造恶意查询导致语法错误或绕过预期行为
        safe_query = escape_fts_query(query)

        # 使用 FTS5 搜索
        rows = await self._fetch_all(
            """SELECT h.* FROM history h
               JOIN history_fts fts ON h.id = fts.id
               WHERE history_fts MATCH ?
               ORDER BY h.created_at DESC
               LIMIT ? OFFSET ?""",
            (safe_query, limit, offset),
        )
        records = [self._row_to_record(row) for row in rows]

        # 获取搜索结果总数
        count_row = await self._fetch_one("SELECT COUNT(*) FROM history_fts WHERE history_fts MATCH ?", (safe_query,))
        total = count_row[0] if count_row else 0

        return records, total

    async def find_by_output_file(self, output_file: str) -> HistoryRecord | None:
        """按输出文件路径反查历史记录（数据治理 P3-1 输出溯源）。

        Args:
            output_file: 输出文件路径（精确匹配）。

        Returns:
            命中的最新一条 HistoryRecord；未找到或空路径返回 None。
        """
        if not output_file:
            return None
        row = await self._fetch_one(
            "SELECT * FROM history WHERE output_file = ? ORDER BY id DESC LIMIT 1",
            (output_file,),
        )
        return self._row_to_record(row) if row else None

    async def distinct_output_dirs(self, limit: int = 50, scan_window: int = 1000) -> list[str]:
        """最近任务输出去重父目录（水位清理范围，成本治理 P1-1）。

        默认输出模板（如 ``{input_dir}/restored/``）会把成品写到 outputs/
        之外，时间清理覆盖不到；水位清理以本方法返回的目录为清理范围。

        Args:
            limit: 返回目录数上限。
            scan_window: 扫描最近多少条带输出路径的记录。

        Returns:
            去重后的输出文件父目录列表（新记录优先，最多 limit 个）。
        """
        rows = await self._fetch_all(
            "SELECT output_file FROM history WHERE output_file != '' ORDER BY id DESC LIMIT ?",
            (int(scan_window),),
        )
        dirs: list[str] = []
        for row in rows:
            parent = os.path.dirname(row["output_file"])
            if parent and parent not in dirs:
                dirs.append(parent)
                if len(dirs) >= limit:
                    break
        return dirs

    async def delete_record(self, record_id: int, soft: bool = True) -> bool:
        """删除记录。

        默认软删除（回收站）：仅标记 deleted_at，记录与输出文件均保留，可恢复。
        ``soft=False`` 时物理删除。

        Returns:
            记录存在且更新/删除成功返回 True。
        """
        if soft:
            ts = datetime.now().isoformat()
            rowcount = await self._execute_write(
                "UPDATE history SET deleted_at = ? WHERE id = ? AND deleted_at IS NULL",
                (ts, record_id),
                want_rowcount=True,
            )
            return rowcount > 0
        await self._execute_write("DELETE FROM history WHERE id = ?", (record_id,))
        return True

    async def get_task_ids_by_record_id(self, record_id: int) -> list[str]:
        """查询历史记录关联的全部任务 ID（数据治理 P1-1 删除连带清理用）。

        一个记录可能因重试/断点续跑存在多个任务行，删除记录时需要
        逐一回收其断点续跑 JSON。

        Args:
            record_id: 历史记录主键 ID。

        Returns:
            关联的 task_id 列表（按更新时间降序）；无关联任务返回空列表。
        """
        rows = await self._fetch_all(
            "SELECT task_id FROM tasks WHERE record_id = ? ORDER BY updated_at DESC",
            (record_id,),
        )
        return [str(row["task_id"]) for row in rows]

    async def set_record_pinned(self, record_id: int, pinned: bool) -> bool:
        """设置/取消记录的「标记保留」（数据治理 P1-5）。

        pinned 记录的输出文件被 retention 年龄/数量清理豁免。

        Args:
            record_id: 历史记录主键 ID。
            pinned: True 标记保留，False 取消标记。

        Returns:
            记录存在且更新成功返回 True；记录不存在返回 False。
        """
        rowcount = await self._execute_write(
            "UPDATE history SET pinned = ? WHERE id = ?",
            (1 if pinned else 0, record_id),
            want_rowcount=True,
        )
        return rowcount > 0

    async def get_pinned_output_paths(self) -> set[str]:
        """查询全部 pinned 记录的输出文件路径（retention 清理豁免清单）。

        Returns:
            去重后的输出文件路径集合（空 output_file 不计入）；无 pinned 记录返回空集合。
        """
        rows = await self._fetch_all("SELECT DISTINCT output_file FROM history WHERE pinned = 1 AND output_file != ''")
        return {str(row["output_file"]) for row in rows}

    async def get_records_filtered(
        self, before_date: str | None = None, status: str | None = None
    ) -> list[HistoryRecord]:
        """按 clear_records 相同条件查询记录（删除连带产物前先取落盘路径）。

        Args:
            before_date: 仅匹配该日期之前的记录，None 不限。
            status: 仅匹配指定状态，None 不限。

        Returns:
            命中的 HistoryRecord 列表。
        """
        conditions = []
        params: list = []
        if before_date:
            conditions.append("created_at < ?")
            params.append(before_date)
        if status:
            conditions.append("status = ?")
            params.append(status)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        rows = await self._fetch_all(f"SELECT * FROM history{where}", params)
        return [self._row_to_record(row) for row in rows]

    async def clear_records(self, before_date: str | None = None, status: str | None = None, soft: bool = True) -> int:
        """清除记录。

        默认软删除（回收站）：标记 deleted_at，记录与文件保留，可经回收站恢复。
        ``soft=False`` 时物理删除（供彻底清空 / 数据治理清理使用）。

        Args:
            before_date: 仅清除此日期之前的记录，为 None 则不限日期。
            status: 仅清除指定状态的记录，为 None 则清除所有状态。
            soft: True=软删除（默认），False=物理删除。
        """
        conditions = []
        params: list = []
        if before_date:
            conditions.append("created_at < ?")
            params.append(before_date)
        if status:
            conditions.append("status = ?")
            params.append(status)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        if soft:
            ts = datetime.now().isoformat()
            # 仅标记未删除的；已软删的不重复更新时间戳
            soft_where = where + (" AND deleted_at IS NULL" if where else " WHERE deleted_at IS NULL")
            return await self._execute_write(
                f"UPDATE history SET deleted_at = ?{soft_where}", [ts, *params], want_rowcount=True
            )
        return await self._execute_write(f"DELETE FROM history{where}", params, want_rowcount=True)

    async def restore_records(self, record_ids: list[int]) -> int:
        """从回收站恢复记录（清除 deleted_at）。"""
        if not record_ids:
            return 0
        placeholders = ", ".join("?" for _ in record_ids)
        return await self._execute_write(
            f"UPDATE history SET deleted_at = NULL WHERE id IN ({placeholders})",
            list(record_ids),
            want_rowcount=True,
        )

    async def list_deleted_records(self, limit: int = 50, offset: int = 0) -> tuple[list[HistoryRecord], int]:
        """列出回收站中的记录（已软删除）。"""
        total_row = await self._fetch_one("SELECT COUNT(*) FROM history WHERE deleted_at IS NOT NULL")
        total = total_row[0] if total_row else 0
        rows = await self._fetch_all(
            "SELECT * FROM history WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC LIMIT ? OFFSET ?",
            [limit, offset],
        )
        return [self._row_to_record(row) for row in rows], total

    async def purge_deleted_records(self, keep_days: int = 30) -> int:
        """彻底清理回收站中超过 keep_days 天的记录（删除 DB 行；输出文件由 retention 负责）。

        超过保留期的软删除记录才物理删除，既防误删又避免回收站无限膨胀。
        """
        cutoff = (datetime.now() - timedelta(days=keep_days)).isoformat()
        # 用 ISO 字符串字典序比较（datetime.isoformat 为定长、可排序）
        row = await self._fetch_one(
            "SELECT COUNT(*) FROM history WHERE deleted_at IS NOT NULL AND deleted_at < ?", (cutoff,)
        )
        count = row[0] if row else 0
        if count:
            await self._execute_write("DELETE FROM history WHERE deleted_at IS NOT NULL AND deleted_at < ?", (cutoff,))
        return count

    async def count_records(self) -> int:
        """统计当前历史记录总数。"""
        row = await self._fetch_one("SELECT COUNT(*) FROM history")
        return int(row[0]) if row else 0

    async def prune_old_records(self, max_records: int | None = None) -> int:
        """按保留上限裁剪最旧的历史记录。

        落实 config.yaml history.max_records 的"超出自动清理旧记录"语义：
        保留最新 max_records 条（按自增 id 降序），删除其余。
        DELETE 会触发 history_ad 触发器，FTS 全文索引自动同步。

        Args:
            max_records: 保留上限。None 时使用构造时传入的 self.max_records。

        Returns:
            实际删除的记录数（0 表示无需裁剪）。
        """
        limit = self.max_records if max_records is None else max_records
        if not limit or limit <= 0:
            return 0
        count = await self.count_records()
        if count <= limit:
            return 0
        # 两步确定式裁剪：先取「保留边界」的最新第 limit 条记录 id，
        # 再删除该 id 之前的所有记录。避免 SQLite 中 DELETE 的 WHERE
        # 含同表子查询时会看到删除中途表状态的歧义行为
        row = await self._fetch_one(
            "SELECT id FROM history ORDER BY id DESC LIMIT 1 OFFSET ?",
            (limit - 1,),
        )
        if row is None:
            return 0
        cutoff_id = int(row[0])
        deleted = await self._execute_write(
            "DELETE FROM history WHERE id < ?",
            (cutoff_id,),
            want_rowcount=True,
        )
        if deleted:
            logger.info(f"历史记录超出上限 {limit}，已自动裁剪最旧 {deleted} 条")
        return deleted or 0

    async def _maybe_prune(self) -> None:
        """写入后按上限裁剪（best-effort，不影响插入主流程）。"""
        try:
            if self.max_records and self.max_records > 0:
                await self.prune_old_records()
        except Exception as e:
            logger.warning(f"历史记录自动裁剪失败: {e}")

    # ==================== 任务状态持久化 ====================

    async def create_task(self, record: TaskRecord) -> bool:
        """创建任务记录"""
        if not record.updated_at:
            record.updated_at = datetime.now().isoformat()

        await self._execute_write(
            """INSERT INTO tasks (task_id, record_id, status, progress, output_path, error_message, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                record.task_id,
                record.record_id,
                record.status,
                record.progress,
                record.output_path,
                record.error_message,
                record.updated_at,
            ),
        )
        return True

    async def update_task(self, task_id: str, **kwargs) -> bool:
        """更新任务记录"""
        if not kwargs:
            return False

        # 列名白名单验证，防止 SQL 注入；SET 子句中的列名来自白名单，不存在注入风险
        allowed_columns = {"record_id", "status", "progress", "output_path", "error_message"}
        invalid_keys = set(kwargs.keys()) - allowed_columns
        if invalid_keys:
            raise ValueError(f"不允许更新的列: {invalid_keys}，允许的列: {allowed_columns}")

        set_clause = ", ".join(f"{k} = ?" for k in kwargs)

        await self._execute_write(
            f"UPDATE tasks SET {set_clause}, updated_at = ? WHERE task_id = ?",
            list(kwargs.values()) + [datetime.now().isoformat(), task_id],
        )
        return True

    async def get_task(self, task_id: str) -> TaskRecord | None:
        """获取单条任务记录"""
        row = await self._fetch_one("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
        if row:
            return self._row_to_task_record(row)
        return None

    async def get_task_by_record_id(self, record_id: int) -> TaskRecord | None:
        """通过历史记录 ID 获取关联任务"""
        row = await self._fetch_one(
            "SELECT * FROM tasks WHERE record_id = ? ORDER BY updated_at DESC LIMIT 1", (record_id,)
        )
        if row:
            return self._row_to_task_record(row)
        return None

    async def get_tasks_by_status(self, status) -> list[TaskRecord]:
        """根据状态获取任务列表；status 可以是单个状态字符串或状态集合"""
        statuses = {status} if isinstance(status, str) else set(status)
        placeholders = ", ".join("?" for _ in statuses)
        params = list(statuses)

        rows = await self._fetch_all(
            f"SELECT * FROM tasks WHERE status IN ({placeholders}) ORDER BY updated_at DESC", params
        )
        return [self._row_to_task_record(row) for row in rows]

    async def delete_task(self, task_id: str) -> bool:
        """删除任务记录"""
        await self._execute_write("DELETE FROM tasks WHERE task_id = ?", (task_id,))
        return True

    async def get_incomplete_tasks(self) -> list[TaskRecord]:
        """获取未完成的任务（pending / processing）"""
        return await self.get_tasks_by_status({"pending", "processing"})

    async def get_statistics(self) -> dict:
        """获取统计信息。

        全部聚合排除软删除（回收站）记录——统计卡描述的是列表页可见的记录集；
        回收站中的记录恢复（清 deleted_at）后自动回到统计，彻底清除后自然消失。
        """
        # 总记录数
        total_row = await self._fetch_one("SELECT COUNT(*) FROM history WHERE deleted_at IS NULL")
        total = total_row[0] if total_row else 0

        # 按类型统计
        type_rows = await self._fetch_all(
            "SELECT task_type, COUNT(*) as cnt FROM history WHERE deleted_at IS NULL GROUP BY task_type"
        )
        by_type: dict[str, int] = {row[0]: row[1] for row in type_rows}

        # 按状态统计
        status_rows = await self._fetch_all(
            "SELECT status, COUNT(*) as cnt FROM history WHERE deleted_at IS NULL GROUP BY status"
        )
        by_status: dict[str, int] = {row[0]: row[1] for row in status_rows}

        # 平均处理时间
        avg_row = await self._fetch_one(
            "SELECT AVG(processing_time) FROM history WHERE status = 'completed' AND deleted_at IS NULL"
        )
        avg_time = avg_row[0] if avg_row and avg_row[0] else 0

        # 成本可见性聚合（P1-1）：总耗时 / 总输出体积
        agg_row = await self._fetch_one("""SELECT COALESCE(SUM(processing_time), 0), COALESCE(SUM(output_size_bytes), 0)
               FROM history WHERE status = 'completed' AND deleted_at IS NULL""")
        total_time = agg_row[0] if agg_row and agg_row[0] else 0
        total_output_bytes = agg_row[1] if agg_row and agg_row[1] else 0

        return {
            "total_records": total,
            "by_type": by_type,
            "by_status": by_status,
            "avg_processing_time": round(avg_time, 2),
            "total_processing_time": round(total_time, 2),
            "total_output_bytes": int(total_output_bytes),
        }

    async def close(self):
        """关闭持久数据库连接"""
        if self._db is not None:
            # ROBUSTNESS: 即使 close() 抛异常，也要确保内部状态被重置 (E7)
            try:
                await self._db.close()
            except (aiosqlite.Error, sqlite3.Error) as e:
                logger.warning(f"关闭数据库连接时出现异常（已忽略）: {e}")
            finally:
                self._db = None
                self._initialized = False
                logger.info(f"历史数据库连接已关闭: {self.db_path}")

    def _row_to_record(self, row: sqlite3.Row) -> HistoryRecord:
        """将数据库行转换为 HistoryRecord"""
        # 老库迁移前列可能不存在：用 keys() 集合兜底（Row 的 in 比较的是值，不能用）
        cols = set(row.keys())
        return HistoryRecord(
            id=row["id"],
            task_type=row["task_type"],
            input_file=row["input_file"],
            output_file=row["output_file"],
            model_size=row["model_size"],
            status=row["status"],
            parameters=row["parameters"],
            processing_time=row["processing_time"],
            created_at=row["created_at"],
            error_message=row["error_message"],
            output_size_bytes=row["output_size_bytes"] if "output_size_bytes" in cols else 0,
            vram_peak_mb=row["vram_peak_mb"] if "vram_peak_mb" in cols else 0.0,
            input_sha256=row["input_sha256"] if "input_sha256" in cols else "",
            input_width=int(row["input_width"]) if "input_width" in cols else 0,
            input_height=int(row["input_height"]) if "input_height" in cols else 0,
            input_duration_sec=float(row["input_duration_sec"]) if "input_duration_sec" in cols else 0.0,
            pinned=bool(row["pinned"]) if "pinned" in cols else False,
        )

    def _row_to_task_record(self, row: sqlite3.Row) -> TaskRecord:
        """将数据库行转换为 TaskRecord"""
        return TaskRecord(
            task_id=row["task_id"],
            record_id=row["record_id"],
            status=row["status"],
            progress=row["progress"],
            output_path=row["output_path"],
            error_message=row["error_message"],
            updated_at=row["updated_at"],
        )
