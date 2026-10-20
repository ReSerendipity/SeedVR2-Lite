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
| `launcher/` | Windows 桌面安装器（release-notes-intro.md 被 CI 读取） |
| `model/` | 权重；`configs_3b/` `configs_7b/` 模型配置 |
| `training/` | 训练脚本 |
| `data/` `outputs/` `logs/` `screenshots/` | 运行/产物 |
| `examples/` | API 用法示例；`demo/` HTML 演示 |

> ⚠️ 两套文档：**`docs/`**（纯开发/报告文档）与 **`website/`**（vitepress 官网站点，含独立 `website/docs/`，面向最终用户说明）。改开发文档进 docs/，改官网进 website/。

## docs/ 索引（本目录）
| 子目录/文件 | 存什么 |
|---|---|
| `project/` | 架构、约束、项目上下文、算法分析、首次使用指南 |
| `plans/` | 实施指南(全功能)、优化、部署、winpython 迁移 |
| `reports/` | 健康度/功能状态、UX-UI 评估、安全AUDIT、LOGGING、config_review 等（维护者本地，未随仓库分发） |
| `repo-analysis/` | 参考仓库学习报告（含综合分析，维护者本地，未随仓库分发） |
| `_devarchive/` | 历史/一次性产物（维护者本地，未随仓库分发） |
| `superpowers/` | 桌面安装器设计(spec/plan，维护者本地，未随仓库分发) |
| `screenshots/` | 界面截图 |
| `SECURITY_AUDIT_REPORT*`（维护者本地）/ `COMPLIANCE_CHECKLIST` | 安全/合规 |

## 想找内容？
- 想改生成/复原逻辑 → `app/integrated_app/engines/`、`common/diffusion/`、`model_lib/`
- 想改前端 → `app/integrated_app/static/`、`templates/`
- 想改配置 → `config.yaml`
- 想了解功能范围 → `docs/reports/功能实现状态分析报告.md`（维护者本地，未随仓库分发）
- 质量门禁：根目录 `run_checks.bat`（勿移动）
