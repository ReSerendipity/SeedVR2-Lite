**ADR-0001: i18n 采用 JSON 词表（无 gettext 体系）**

> 本地未分发引用：AGENTS.md（维护者本地治理文件，未随仓库分发；本决策记录的是针对它的历史修订）。

- **状态**: Implemented
- **日期**: 2026-08-27
- **决策者**: 项目维护者 + AI 指挥（家族规范审计 Phase A 确认）

---

# 背景与问题

AGENTS.md 原样继承了 TTS 的 gettext 描述（`common/locale/<lang>/LC_MESSAGES/messages.{po,mo}`、
`msgmerge`/`msgfmt`、`scripts/update_pot.py`）。实测（2026-08-27）：全仓**无任何 `.po`/`.mo`/`.pot` 文件**，
gettext 体系从未实现；真实多语言存储为 `app/integrated_app/locales/*.json`，共 **5 个词表**（zh / en / ja / fr / zh-TW）。

# 决策

- i18n 技术选型为 **JSON 词表**；删除/改写 AGENTS.md 中全部 gettext 流程描述（含虚构的 `update_pot.py`）。
- 新增语言按既有 JSON 词表结构扩展，禁止回写 gettext 相关承诺（防复发铁律 #6）。

# 实施影响

- AGENTS.md §i18n 章节按 JSON 实现重写；`tests/integration` 幻影命令随之修正（见 ADR-0002）。

# 可回滚路径与待验证项

- 纯文档决策；待验证：审计器对 `common/locale`、`messages.po`、`update_pot.py` 零命中。
