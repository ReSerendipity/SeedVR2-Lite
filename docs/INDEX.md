# 文档索引（docs/INDEX）

> 最后核对：2026-11-10　｜　维护：文档整理工单 WO-DOC-B01
> 本文件是 `docs/` 全量 tracked Markdown 的机器可读索引（46 篇：顶层 27 + `adr/` 7 + `project/` 6 + `plans/` 1 + `_archive/` 5）。
> [`README.md`](README.md) 保留为人类阅读的入口速览（目录结构、分组导航、「想找内容」路由）；两者冲突时以本文件为准。
> `_devarchive/`（8 篇，维护者本地、被 `.gitignore` 忽略）不在 tracked 范围内，只做目录级说明。
> ⚠️ **另一套文档是官网**：`website/docs/`（VitePress，面向最终用户）。改开发文档进 `docs/`，改官网进 `website/`。

## 状态口径

| 状态 | 含义 |
|---|---|
| 现行 | 仍在指导当前工作，或仍是某项规则、SOP、Schema、口径的有效来源 |
| 历史 | 时点记录（报告、审计、发布验收、诊断留痕），只作追溯 |
| 废弃 | 已被后继文档取代，或文件自身标注 Superseded / 已被 xxx 取代 |
| WIP | 文件自述未定稿，尚在演进中 |

## 一、仓根入口文档

| 文档 | 用途 | 状态 | 核对日期 |
|---|---|---|---|
| [`README.md`](../README.md) | 面向用户的项目说明：超分/修复能力、界面截图、安装与模型下载 | 现行 | 2026-11-10 |
| [`AGENTS.md`](../AGENTS.md) | 编码代理在本仓的执行契约（维护者本地文件，**未随仓库分发**） | 现行 | 2026-11-10 |
| [`CHANGELOG.md`](../CHANGELOG.md) | 版本变更记录 | 现行 | 2026-11-10 |
| [`CONTRIBUTING.md`](../CONTRIBUTING.md) | 贡献指南 | 现行 | 2026-11-10 |
| [`LOCAL_RULES.md`](../LOCAL_RULES.md) | 本机环境相关的本地约定 | 现行 | 2026-11-10 |
| [`LICENSE`](../LICENSE) | 项目主许可（Apache-2.0） | 现行 | 2026-11-10 |
| [`NOTICE`](../NOTICE) | 模型权重与 engine 组件的归属声明 | 现行 | 2026-11-10 |

## 二、docs/ 顶层（安全、规范、发布运维、中文分发文档）

| 文档 | 用途 | 状态 | 核对日期 |
|---|---|---|---|
| [`README.md`](README.md) | docs 入口速览：目录结构、分组导航与「想找内容」路由 | 现行 | 2026-11-10 |
| [`COMPLIANCE_CHECKLIST.md`](COMPLIANCE_CHECKLIST.md) | 对外服务合规检查清单 | 现行 | 2026-11-10 |
| [`DATA_GOVERNANCE_ASSESSMENT.md`](DATA_GOVERNANCE_ASSESSMENT.md) | 数据管理与治理体系深度完整性评估报告 | 历史 | 2026-11-10 |
| [`LICENSE_COMPLIANCE.md`](LICENSE_COMPLIANCE.md) | 许可证合规台账 | 现行 | 2026-11-10 |
| [`PRIVACY_POLICY.md`](PRIVACY_POLICY.md) | 隐私政策 | 现行 | 2026-11-10 |
| [`SECURITY_MATRIX.md`](SECURITY_MATRIX.md) | 安全矩阵（控制措施对照） | 现行 | 2026-11-10 |
| [`SECURITY_REMEDIATION_TRACKER.md`](SECURITY_REMEDIATION_TRACKER.md) | 安全整改追踪表 | 现行 | 2026-11-10 |
| [`SECURITY_REVIEW_DESKTOP_SHELL.md`](SECURITY_REVIEW_DESKTOP_SHELL.md) | 桌面壳（`desktop/`）聚焦安全评估（R10 · 2026-09-06） | 历史 | 2026-11-10 |
| [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) | 第三方组件声明（非穷尽清单） | 现行 | 2026-11-10 |
| [`USER_AGREEMENT.md`](USER_AGREEMENT.md) | 用户协议 | 现行 | 2026-11-10 |
| [`AI应用分发安全加固指南.md`](AI应用分发安全加固指南.md) | AI 应用分发安全加固实战复盘 | 历史 | 2026-11-10 |
| [`CODING_STANDARDS.md`](CODING_STANDARDS.md) | 入库内容的可移植性、依赖与 Git 卫生约束 | 现行 | 2026-11-10 |
| [`DOD.md`](DOD.md) | 完成定义（Definition of Done） | 现行 | 2026-11-10 |
| [`METRICS_SPEC.md`](METRICS_SPEC.md) | 指标口径规范（数据治理 P3-2 的单一事实来源） | 现行 | 2026-11-10 |
| [`ML_OPS_GATES.md`](ML_OPS_GATES.md) | MLOps 质量门禁说明（覆盖率为何只统计 Web 层、引擎核心如何兜底） | 现行 | 2026-11-10 |
| [`agent-contract.md`](agent-contract.md) | Agent 契约（与 `.github` 治理模板逐字节一致，勿本地改） | 现行 | 2026-11-10 |
| `SKILL.md` | Bilibili 开源仓库批量采集与去重入库（skill 定义；2026-11-10 核实：该文件未随仓库分发，故不做链接） | 未分发 | 2026-11-10 |
| [`CSP_TIGHTENING_ROADMAP.md`](CSP_TIGHTENING_ROADMAP.md) | CSP 收紧路线图（R8 · 2026-09-06；进度见文内进度节） | 现行 | 2026-11-10 |
| [`release-governance.md`](release-governance.md) | 发布 / 回滚 / SLA 规范 | 现行 | 2026-11-10 |
| [`rollback_sop.md`](rollback_sop.md) | 回滚 SOP（自动化回滚 / 版本化回滚） | 现行 | 2026-11-10 |
| [`SRE_RUNBOOK.md`](SRE_RUNBOOK.md) | SRE 运行手册（SLA / SLO / 回滚 / 灾难恢复演练） | 现行 | 2026-11-10 |
| [`增量更新发布手册.md`](增量更新发布手册.md) | 小版本增量更新发布 SOP（示例版本号 v1.5.3 / v1.5.7 为历史实操） | 现行 | 2026-11-10 |
| [`发布与分发-单文件安装包.md`](发布与分发-单文件安装包.md) | 单文件安装包 + 环境/模型自助下载（2026-10-31 定案的现行分发形态） | WIP | 2026-11-10 |
| [`用户手册.md`](用户手册.md) | 最终用户手册（安装 / 使用 / FAQ） | 现行 | 2026-11-10 |
| [`开发者指南.md`](开发者指南.md) | 开发者指南（本地起服务、改代码、跑测试） | 现行 | 2026-11-10 |
| [`桌面应用分发全流程指南.md`](桌面应用分发全流程指南.md) | AI 应用桌面化分发全流程复盘（历史：多文件分卷时代） | 历史 | 2026-11-10 |
| [`输出保留策略.md`](输出保留策略.md) | 输出保留策略（成本资源治理 P0-1 / P1-1） | 现行 | 2026-11-10 |

## 三、docs/project/（架构与项目上下文）

| 文档 | 用途 | 状态 | 核对日期 |
|---|---|---|---|
| [`project/ARCHITECTURE.md`](project/ARCHITECTURE.md) | 分层架构、模块关系、请求流程与核心设计决策 | 现行 | 2026-11-10 |
| [`project/CONSTRAINTS.md`](project/CONSTRAINTS.md) | 项目约束（技术栈 / 依赖 / 运行环境边界） | 现行 | 2026-11-10 |
| [`project/FIRST_TIME_USER_GUIDE.md`](project/FIRST_TIME_USER_GUIDE.md) | 首次使用指南 | 现行 | 2026-11-10 |
| [`project/MODULE_MAP.md`](project/MODULE_MAP.md) | 模块边界与关键规则（2026-08-27 由 `AGENTS.md` 移出，内容逐字保留） | 现行 | 2026-11-10 |
| [`project/PROJECT_CONTEXT.md`](project/PROJECT_CONTEXT.md) | 项目高频背景信息（给代理与维护者） | 现行 | 2026-11-10 |
| [`project/model_algorithm_analysis.md`](project/model_algorithm_analysis.md) | 模型算法复杂度分析 | 历史 | 2026-11-10 |

## 四、docs/adr/（架构决策记录）

| 文档 | 用途 | 状态 | 核对日期 |
|---|---|---|---|
| [`adr/README.md`](adr/README.md) | ADR 索引与格式规范（编号唯一、只追加不删除，被推翻标 Superseded） | 现行 | 2026-11-10 |
| [`adr/0001-json-locales-no-gettext.md`](adr/0001-json-locales-no-gettext.md) | ADR-0001：i18n 采用 JSON 词表（无 gettext 体系） | 现行 | 2026-11-10 |
| [`adr/0002-test-layering-actual.md`](adr/0002-test-layering-actual.md) | ADR-0002：测试分层实测（pytest specs + Playwright） | 现行 | 2026-11-10 |
| [`adr/0003-tauri-desktop-shell-migration.md`](adr/0003-tauri-desktop-shell-migration.md) | ADR-0003：对外分发形态采用 Tauri v2 桌面壳（追溯记录） | 现行 | 2026-11-10 |
| [`adr/0004-incremental-updater-trust-root.md`](adr/0004-incremental-updater-trust-root.md) | ADR-0004：应用代码增量更新器与分层发布信任根（追溯记录） | 现行 | 2026-11-10 |
| [`adr/0005-event-bus-vs-progress-listeners.md`](adr/0005-event-bus-vs-progress-listeners.md) | ADR-0005：事件总线不引入 EventBus（保留现有双层结构） | 现行 | 2026-11-10 |
| [`adr/0006-gaussian-blend-not-adopted.md`](adr/0006-gaussian-blend-not-adopted.md) | ADR-0006：不引入 Gaussian 混合分块（tile 融合由上游 raised-cosine 负责） | 现行 | 2026-11-10 |

## 五、docs/plans/（实施与部署）

| 文档 | 用途 | 状态 | 核对日期 |
|---|---|---|---|
| [`plans/DEPLOYMENT.md`](plans/DEPLOYMENT.md) | 部署文档（环境准备、服务启动、端口与反代） | 现行 | 2026-11-10 |

> `plans/` 下其余实施指南（全功能、优化、winpython 迁移等）为维护者本地文档，未随仓库分发。

## 六、docs/_archive/（历史记录）

| 文档 | 用途 | 状态 | 核对日期 |
|---|---|---|---|
| [`_archive/发布检查清单.md`](_archive/发布检查清单.md) | 桌面版发布验收与修复日志（v1.5.1 时代，检查时间 2026-09-05） | 历史 | 2026-11-10 |
| [`_archive/壳更新-下一轮改动清单.md`](_archive/壳更新-下一轮改动清单.md) | 壳更新执行记录（随 v1.5.3 全部执行并验证，2026-09-08） | 历史 | 2026-11-10 |
| [`_archive/INSTALLER-LESSONS.md`](_archive/INSTALLER-LESSONS.md) | Inno Setup & CI 编译经验教训（v1.4.8 分卷打包）；2026-10-21 注记已被 Tauri v2 方案取代 | 废弃 | 2026-11-10 |
| [`_archive/VAE_STALL_DIAGNOSIS_20260910.md`](_archive/VAE_STALL_DIAGNOSIS_20260910.md) | VAE 阶段卡死 + 显存占用诊断报告（2026-09-10） | 历史 | 2026-11-10 |
| [`_archive/2026-09-agent-memory-archive.md`](_archive/2026-09-agent-memory-archive.md) | 2026-09 会话记忆归档（Git 损坏恢复 / 测试 flake / 发版事故 / v1.6.0 runbook） | 历史 | 2026-11-10 |

## 七、其他文档区域

| 区域 | 用途 | 状态 | 核对日期 |
|---|---|---|---|
| [`docs/_devarchive/`](_devarchive/) | 历史/一次性产物 8 篇（`trae-documents/` 7 + 1 篇），维护者本地、被 `.gitignore` 忽略，**未随仓库分发** | 历史 | 2026-11-10 |
| [`website/docs/`](../website/docs/) | VitePress 官网站点 14 篇（面向最终用户：install / usage / models / vram / faq / security 等），与 `docs/` 分立 | 现行 | 2026-11-10 |
| [`tests/`](../tests/) | 测试说明与行为测试报告 7 篇（含 `USER_BEHAVIOR_TEST_REPORT.md`） | 历史 | 2026-11-10 |
| [`app/`](../app/)、[`model_lib/`](../model_lib/) | 组件级说明 2 + 2 篇（含 `model_lib/SOURCE.md` 上游内核归属） | 现行 | 2026-11-10 |
| [`launcher/`](../launcher/)、[`portable_launcher/`](../portable_launcher/) | 启动器说明 2 篇（`release-notes-portable.md` 被 `portable-release.yml` 读取，改动需同步 CI 期望） | 现行 | 2026-11-10 |
| [`local/`](../local/) | 本地维护层 112 篇（含 `_incoming/` 审核材料与 `agents/` 台账），**未随仓库分发** | 历史 | 2026-11-10 |
| [`_archive/`](../_archive/)、[`reports/`](../reports/)、[`demo/`](../demo/)、[`desktop/`](../desktop/)、[`dogfood-output/`](../dogfood-output/)、[`examples/`](../examples/)、[`perf/`](../perf/) | 项目级归档、报告与示例说明 9 篇 | 历史 | 2026-11-10 |

## 八、索引维护规则

1. **新增文档**：归入对应目录并在本文件相应节补一行；`状态` 按上文口径判定；同时更新 [`README.md`](README.md) 的分组表。
2. **取代 / 归档**：被取代的文档在行内把 `状态` 改为 `废弃` 并写后继文档路径；需移走的入 `_archive/docs-archive-<YYYYMMDD>/`（保留原相对路径），**不删除**；`docs/_archive/` 已存在，沿用同一目录即可。
3. **本地层文档**：`_devarchive/`、`local/`、`AGENTS.md` 等未随仓库分发的文件只做目录级说明；引用时必须标注「维护者本地文件，未随仓库分发」。
4. **核对日期**：整仓复核时统一刷新。
