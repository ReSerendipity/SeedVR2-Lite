**ADR-0002: 测试分层实测（pytest specs + Playwright）**

> 本地未分发引用：AGENTS.md（维护者本地治理文件，未随仓库分发；本决策记录的是针对它的历史修订）。

- **状态**: Implemented
- **日期**: 2026-08-27
- **决策者**: 项目维护者 + AI 指挥（家族规范审计 Phase A 确认）

---

# 背景与问题

AGENTS.md 测试表曾声明「集成测试：`pytest tests/integration -q` 必须全部通过」。
实测：`tests/integration/` **不存在**，照做会直接报错；真实测试结构为
`tests/specs/`（pytest 规格测试）、`tests/frontend/`、`tests/pages/`、`tests/perf/` 及
Playwright 配置 `tests/playwright.config.ts`。

# 决策

- 测试分层以实测目录为准：
  - Python 侧：`pytest tests/specs -q`（及 frontend/pages/perf 对应子集）；
  - 浏览器集成：`npx playwright test -c tests/playwright.config.ts`（不计入 fail_under，但必须全部通过）。
- 引擎层按真实文件结构描述（`app/integrated_app/engines/` 下 `seedvr2_engine.py` 与各 `_*_pipeline.py`），
  删除从 TTS 移植的「自动注册 / BaseXxxProtocol 三实现」等不适用表述。

# 实施影响

- AGENTS.md 测试表改用真实命令；CONTRIBUTING 与 SECURITY_AUDIT_REPORT 中引用同步。

# 可回滚路径与待验证项

- 纯文档决策；待验证：Playwright 配置文件名（`.ts` 而非 `.js`）与 pytest collect 结果一致。
