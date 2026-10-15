# Architecture Decision Records (ADR)

> 本目录是 SeedVR2-lite 项目的架构决策记录库。
> 每个 ADR 编号唯一、只追加不删除（被推翻的标记为 Superseded）。

## 索引

| 编号 | 标题 | 状态 | 日期 |
|------|------|------|------|
| [0001](0001-json-locales-no-gettext.md) | i18n 采用 JSON 词表（无 gettext 体系） | Implemented | 2026-08-27 |
| [0002](0002-test-layering-actual.md) | 测试分层实测（pytest specs + Playwright） | Implemented | 2026-08-27 |
| [0003](0003-tauri-desktop-shell-migration.md) | 对外分发形态采用 Tauri v2 桌面壳（追溯） | Implemented | 2026-10-15 |
| [0004](0004-incremental-updater-trust-root.md) | 应用代码增量更新器与分层发布信任根（追溯） | Implemented | 2026-10-15 |

## 何时必须写 ADR（满足任一即写）

- 引入或移除一个**运行时依赖**（模型引擎、数据库、前端框架）
- 改变**目录结构或分层边界**
- 做出**有长期代价的技术选择**
- 撤销此前的 ADR → 新写一条并把旧条状态改为 `Superseded by NNNN`

## ADR 格式规范

每个 ADR 文件应包含以下一级标题：

1. **背景与问题** — 为什么需要做这个决策
2. **评估的备选方案** — 列出所有考虑过的方案及其利弊
3. **决策** — 最终选择了什么方案，以及选择理由
4. **实施影响** — 对代码、依赖、文档的具体影响清单
5. **可回滚路径与待验证项** — 如何回滚、需要验证什么
