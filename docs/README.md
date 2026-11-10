# SeedVR2-lite — 文档与项目速览

> 图像/视频超分与修复（Restore）Web 应用。FastAPI + HTMX。
> 入口：`app/integrated_app/app_server.py` / `start.bat`。
> 详细目录放置规则见 `AGENTS.md`（维护者本地文件，未随仓库分发）末尾「文件归档与放置规范」。

## 快速了解本项目
- **做什么**：图像/视频超分（Super-Resolution）、去噪、修复（SeedVR2 模型）。
- **技术栈**：Python · FastAPI · HTMX · 原生 JS/CSS · CUDA。
- **如何启动**：`start.bat`；桌面安装器见 `launcher/`。

## 目录结构速览
| 目录 | 内容 |
|---|---|
| `app/integrated_app/` | **主程序源码**（engines/ routes/ middleware/ optimization/ security/ static/） |
| `common/` `model_lib/` | 核心算法/模型源码库 |
| `tests/` | pytest + Playwright(ts) + `tests/perf` |
| `docs/` + `website/` | 项目文档（见下方说明） |
| `launcher/` | 便携启动器（release-notes-portable.md 被 portable-release.yml 读取） |
| `model/` | 权重；`configs_3b/` `configs_7b/` 模型配置 |
| `training/` | 训练脚本 |
| `data/` `outputs/` `logs/` `screenshots/` | 运行/产物 |
| `examples/` | API 用法示例；`demo/` HTML 演示 |

> ⚠️ 两套文档：**`docs/`**（纯开发/报告文档）与 **`website/`**（vitepress 官网站点，含独立 `website/docs/`，面向最终用户说明）。改开发文档进 docs/，改官网进 website/。

## docs/ 索引（本目录）

本文件即 `docs/README.md`，为 docs/ 的速览入口。下表覆盖 `docs/` 下全部 46 篇 tracked Markdown（含子目录；另有 8 篇 `_devarchive/` 本地文档未随仓库分发），链接均为相对路径、可直接点击；「状态/截至」摘自各文自述定位（2026-11-04 复核）。**全量逐文件清单（含路径 / 用途 / 状态 / 核对日期 2026-11-10）见 [`INDEX.md`](INDEX.md)，两者冲突时以 `INDEX.md` 为准。**

### 架构与项目上下文（project/）
| 文件 | 管什么 | 状态/截至 |
|---|---|---|
| [project/ARCHITECTURE.md](project/ARCHITECTURE.md) | 架构文档 | 现行 |
| [project/CONSTRAINTS.md](project/CONSTRAINTS.md) | 项目约束 | 现行 |
| [project/FIRST_TIME_USER_GUIDE.md](project/FIRST_TIME_USER_GUIDE.md) | 首次使用指南 | 现行 |
| [project/MODULE_MAP.md](project/MODULE_MAP.md) | 模块边界与关键规则 | 现行（版本句 2026-11-04 复核） |
| [project/PROJECT_CONTEXT.md](project/PROJECT_CONTEXT.md) | 项目上下文 | 现行 |
| [project/model_algorithm_analysis.md](project/model_algorithm_analysis.md) | 模型算法复杂度分析 | 现行 |

### 架构决策记录（adr/）
| 文件 | 管什么 | 状态/截至 |
|---|---|---|
| [adr/README.md](adr/README.md) | ADR 索引 | 现行 |
| [adr/0001-json-locales-no-gettext.md](adr/0001-json-locales-no-gettext.md) | i18n 采用 JSON 词表（无 gettext 体系） | Implemented（2026-08-27） |
| [adr/0002-test-layering-actual.md](adr/0002-test-layering-actual.md) | 测试分层实测（pytest + Playwright） | Implemented（2026-08-27） |
| [adr/0003-tauri-desktop-shell-migration.md](adr/0003-tauri-desktop-shell-migration.md) | 对外分发形态采用 Tauri v2 桌面壳（追溯） | Implemented（2026-10-15） |
| [adr/0004-incremental-updater-trust-root.md](adr/0004-incremental-updater-trust-root.md) | 增量更新器与分层发布信任根（追溯） | Implemented（2026-10-15） |
| [adr/0005-event-bus-vs-progress-listeners.md](adr/0005-event-bus-vs-progress-listeners.md) | 事件总线不引入 EventBus（保留双层结构） | Implemented（2026-10-28） |
| [adr/0006-gaussian-blend-not-adopted.md](adr/0006-gaussian-blend-not-adopted.md) | 不引入 Gaussian 混合分块 | Implemented（2026-10-28） |

### 实施与部署（plans/）
| 文件 | 管什么 | 状态/截至 |
|---|---|---|
| [plans/DEPLOYMENT.md](plans/DEPLOYMENT.md) | 部署文档 | 现行 |
| `plans/` 其余文档 | 实施指南（全功能）、优化、winpython 迁移等 | 维护者本地，未随仓库分发 |

### 安全与合规
| 文件 | 管什么 | 状态/截至 |
|---|---|---|
| [COMPLIANCE_CHECKLIST.md](COMPLIANCE_CHECKLIST.md) | 对外服务合规检查清单 | 现行 |
| [DATA_GOVERNANCE_ASSESSMENT.md](DATA_GOVERNANCE_ASSESSMENT.md) | 数据管理与治理完整性评估 | 现行 |
| [LICENSE_COMPLIANCE.md](LICENSE_COMPLIANCE.md) | 许可证合规台账 | 现行 |
| [PRIVACY_POLICY.md](PRIVACY_POLICY.md) | 隐私政策 | 现行 |
| [SECURITY_MATRIX.md](SECURITY_MATRIX.md) | 安全矩阵 | 现行 |
| [SECURITY_REMEDIATION_TRACKER.md](SECURITY_REMEDIATION_TRACKER.md) | 安全整改追踪表 | 现行 |
| [SECURITY_REVIEW_DESKTOP_SHELL.md](SECURITY_REVIEW_DESKTOP_SHELL.md) | 桌面壳聚焦安全评估（R10 · 2026-09-06） | 现行 |
| [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) | 第三方组件声明 | 现行 |
| [USER_AGREEMENT.md](USER_AGREEMENT.md) | 用户协议 | 现行 |
| [AI应用分发安全加固指南.md](AI应用分发安全加固指南.md) | AI 应用分发安全加固实战复盘 | 现行 |
| `SECURITY_AUDIT_REPORT*`（维护者本地） | 安全审计报告 | 未随仓库分发 |

### 工程规范与流程
| 文件 | 管什么 | 状态/截至 |
|---|---|---|
| [CODING_STANDARDS.md](CODING_STANDARDS.md) | 编码与 Git 规范 | 现行 |
| [DOD.md](DOD.md) | 完成定义（Definition of Done） | 现行 |
| [METRICS_SPEC.md](METRICS_SPEC.md) | 指标口径规范 | 现行 |
| [ML_OPS_GATES.md](ML_OPS_GATES.md) | MLOps 质量门禁说明 | 现行 |
| [agent-contract.md](agent-contract.md) | Agent 契约（v2） | 现行 |
| [SKILL.md](SKILL.md) | Bilibili 开源仓库批量采集与去重入库（skill） | 现行 |
| [CSP_TIGHTENING_ROADMAP.md](CSP_TIGHTENING_ROADMAP.md) | CSP 收紧路线图（评估报告 R8 · 2026-09-06） | 进度见文内进度节（S1 完成） |

### 发布 / 运维 / 治理
| 文件 | 管什么 | 状态/截至 |
|---|---|---|
| [release-governance.md](release-governance.md) | 发布 / 回滚 / SLA 规范 | 现行（版本位句 2026-11-04 复核） |
| [rollback_sop.md](rollback_sop.md) | 回滚 SOP（自动化回滚 / 版本化回滚） | 现行 |
| [SRE_RUNBOOK.md](SRE_RUNBOOK.md) | SRE 运行手册（SLA / SLO / 回滚 / 灾难恢复演练） | 现行（适用版本句 2026-11-04 复核） |
| [增量更新发布手册.md](增量更新发布手册.md) | 小版本增量更新发布 SOP（可复用） | 现行（示例版本号 v1.5.3 / v1.5.7 为历史实操） |
| [发布与分发-单文件安装包.md](发布与分发-单文件安装包.md) | 单文件安装包 + 环境/模型自助下载 | **WIP，未定稿**（2026-10-31 定案的现行分发形态） |

### 中文分发与用户文档
| 文件 | 管什么 | 状态/截至 |
|---|---|---|
| [用户手册.md](用户手册.md) | 最终用户手册（安装 / 使用 / FAQ） | 适用 Tauri 桌面版 v1.5.1+（当前发布 1.7.0，截至 2026-11-04） |
| [开发者指南.md](开发者指南.md) | 开发者指南 | 现行 |
| [桌面应用分发全流程指南.md](桌面应用分发全流程指南.md) | AI 应用桌面化分发全流程实战复盘 | 历史复盘（多文件分卷时代；现行形态见单文件安装包文档） |
| [输出保留策略.md](输出保留策略.md) | 输出保留策略（成本资源治理 P0-1 / P1-1） | 现行 |

### 历史记录
| 文件 | 管什么 | 状态/截至 |
|---|---|---|
| [_archive/发布检查清单.md](_archive/发布检查清单.md) | 桌面版发布验收与修复日志 | 归档（2026-11-04）；历史记录（v1.5.1 时代，检查时间 2026-09-05） |
| [_archive/壳更新-下一轮改动清单.md](_archive/壳更新-下一轮改动清单.md) | 壳更新执行记录 | 归档（2026-11-04）；历史记录（v1.5.3 已执行，2026-09-08） |
| [_archive/INSTALLER-LESSONS.md](_archive/INSTALLER-LESSONS.md) | Inno Setup & CI 编译经验教训（v1.4.8 分卷打包） | 归档（2026-11-04）；已被 Tauri v2 方案取代（2026-10-21 注记） |
| [_archive/VAE_STALL_DIAGNOSIS_20260910.md](_archive/VAE_STALL_DIAGNOSIS_20260910.md) | VAE 阶段卡死 + 显存占用诊断报告 | 归档（2026-11-04）；历史诊断（2026-09-10） |
| [_archive/2026-09-agent-memory-archive.md](_archive/2026-09-agent-memory-archive.md) | 2026-09 会话记忆归档（Git 损坏恢复 / 测试 flake / 发版事故 / v1.6.0 runbook） | 归档（2026-11-04）；历史记录（2026-09-06 ~ 09-30） |

### 维护者本地 / 未随仓库分发的目录
| 目录 | 管什么 | 状态/截至 |
|---|---|---|
| `_devarchive/` | 历史/一次性产物（维护者本地，未随仓库分发） | 本地存在（被 .gitignore 忽略，不入库） |

## 想找内容？
- 想改生成/复原逻辑 → `app/integrated_app/engines/`、`common/diffusion/`、`model_lib/`
- 想改前端 → `app/integrated_app/static/`、`templates/`
- 想改配置 → `config.yaml`
- 想了解功能范围 → `docs/reports/功能实现状态分析报告.md`（维护者本地，未随仓库分发）（2026-11-04 复核：本克隆不存在，待 owner 确认去向）
- 质量门禁：根目录 `run_checks.bat`（勿移动；维护者本地脚本，未随仓库分发）
