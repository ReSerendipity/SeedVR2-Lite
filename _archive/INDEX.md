# SeedVR2-Lite 归档索引（_archive/）

> 归档区按治理规范 §12.1 使用仓根 `_archive/`（下划线前缀＝非活动区，§10 审计自动豁免）。
> 归档只移动文件、不改写正文；`docs/SRE_RUNBOOK.md` 中的执行记录链接已改新路径。
> 本仓另有 `docs/_archive/`（5 篇）与 `docs/_devarchive/`（8 篇）两处存量归档区——按 §13.1「已在 docs/ 下的存量历史文档不迁移、保留原位」不并入本索引，此处仅登记其存在。

| 原路径 | 归档后路径 | 归档日 | 类别 | 一句话结论 | 是否仍被引用 |
|---|---|---|---|---|---|
| `docs/DR演练记录-history-db-2026-11-06.md` | `_archive/执行记录/2026-11/DR演练记录-history-db-2026-11-06.md` | 2026-11-08 | 执行记录（演练） | history_db 损坏场景**首轮**灾备演练实测（此前全仓只有计划、无执行记录，评估报告 O4/D6 门禁两轮 fail）：恢复 + 复验端到端 RTO 实测 0.490 s、RPO 0 s，全链路通过；恢复验收口径为逻辑对平，非字节级 sha256 | 是：`docs/SRE_RUNBOOK.md:56`（已改新路径） |

## 未归档（活动文档，判定理由）

| 文档 | 保留理由 |
|---|---|
| `docs/DATA_GOVERNANCE_ASSESSMENT.md` | `docs/README.md:62` 标「现行」；被 `docs/AI应用分发安全加固指南.md`、`docs/_archive/2026-09-agent-memory-archive.md` 引用，且 2026-09-21 已按代码现实对账更新——属现行评估台账而非历史快照 |
| `docs/SECURITY_REVIEW_DESKTOP_SHELL.md` | `docs/README.md:67` 标「现行」；桌面壳安全评估的结论仍作回归背书使用 |
| `docs/CSP_TIGHTENING_ROADMAP.md` | 路线图仍在执行中（S1/S2 已落地、**S4 暂停执行**待可见性状态机重构），文内含进度节；并被 `middleware/security_headers.py:39`、`templates/base.html:7`、`templates/history.html:447` 三处源码注释锚定 |
| `docs/plans/DEPLOYMENT.md` | 部署指南（systemd / Nginx / Docker / 多用户 / 备份），现行可操作 |
| `docs/SRE_RUNBOOK.md`、`docs/rollback_sop.md`、`docs/DOD.md`、`docs/ML_OPS_GATES.md`、`docs/METRICS_SPEC.md`、`docs/CODING_STANDARDS.md`、`docs/agent-contract.md`、`docs/release-governance.md`、`docs/SKILL.md` | 现行手册 / 规范 |
| `docs/SECURITY_REMEDIATION_TRACKER.md`、`docs/COMPLIANCE_CHECKLIST.md`、`docs/SECURITY_MATRIX.md` | 台账与矩阵（长期维护） |
| `docs/PRIVACY_POLICY.md`、`docs/USER_AGREEMENT.md`、`docs/THIRD_PARTY_NOTICES.md`、`docs/LICENSE_COMPLIANCE.md` | 法律 / 合规文本 |
| `docs/AI应用分发安全加固指南.md`、`docs/增量更新发布手册.md`、`docs/桌面应用分发全流程指南.md`、`docs/开发者指南.md`、`docs/用户手册.md`、`docs/输出保留策略.md`、`docs/发布与分发.md` | 面向使用者 / 开发者的操作手册 |
| `docs/README.md`、`docs/adr/*`（7 篇）、`docs/project/*`（6 篇） | 索引 / ADR（§12.5 条款 8：ADR 一律不迁移）/ 项目文档 |

## 例外登记

| 位置 | 说明 |
|---|---|
| `docs/_archive/*`（5 篇）、`docs/_devarchive/*`（8 篇） | 存量归档区，按 §13.1 保留原位，不迁移至仓根 `_archive/` |
| `CHANGELOG.md` 中对 `docs/DATA_GOVERNANCE_ASSESSMENT.md` 的历史提及 | 发布历史记录，按既定口径不改写正文，故保留旧表述 |
