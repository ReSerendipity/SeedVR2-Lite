# 完成定义（Definition of Done）

> **来源**：家族通用 DOD 模板（源自 SpiritPal definition-of-done.md 泛化的家族模板，原 `.spec_audit/family_DOD.md` 已随家族归档，本文为本仓本地化副本）。
> **适用范围**：SeedVR2-Turbo 全项目所有功能开发任务。
> **本地未分发引用**：`AGENTS.md`、`docs/agents/`、`precheck.ps1` 属维护者本地治理层，未随仓库分发
> （`docs/project/` 自 2026-10-20 起已有 6 个文件随仓库分发，其余仍未跟踪）；
> 其对外可执行部分（禁区与门禁口径）已摘入 `docs/CODING_STANDARDS.md` 第 5 节，克隆后以该节为准。

---

## 0. DoD 等级

| 等级 | 适用场景 | 要求 |
|------|---------|------|
| **Full DoD** | 正式功能开发 / 大型 PR | 全部检查类 |
| **Lite DoD** | Bug 修复 / 小优化 | 代码完成 + 测试覆盖 + 构建验证 |
| **Hotfix DoD** | 紧急线上修复 | 代码完成 + 构建验证（事后补齐其余） |

> 判定「完成」必须先跑对应等级清单；不满足不得标注完成、不得提交 main。

## 1. 代码完成

- [ ] 功能已实现，覆盖 PRD / Spec 定义的所有验收标准（AC）
- [ ] lint 通过：`ruff` / `ruff-format`（Python）+ `eslint` / `tsc`（前端 TS）0 error
- [ ] 无调试残留（`print()` / `console.log` / `breakpoint()`，诊断日志除外需标记）
- [ ] 未引入跨层违规引用（禁区与模块边界见 `docs/CODING_STANDARDS.md` §5.1）
- [ ] 新增路由遵守路由自动发现 / engines / middleware 分层约定
- [ ] 涉及引擎改动时遵守对应 SOP（新增引擎 / 调试 / 发布 / 便携包 / 量化精度）；公开子集见 `docs/CODING_STANDARDS.md` §5，完整 SOP 在维护者本地的 `AGENTS.md`

## 2. 测试覆盖

- [ ] 新增/修改函数有对应 pytest 单测（`tests/`）+ 前端 TS spec（Playwright）
- [ ] 覆盖正常路径 + 边界条件 + 异常场景
- [ ] 全量 `pytest` 通过（不新增失败用例）；前端 `npx playwright test` 通过
- [ ] GPU 依赖模块（engine/ optimization/ vram/）在无 GPU 环境有 mock 覆盖，避免 CI 全 skip
- [ ] 涉及金标准质量时确认 `tests/golden/` 基线未退化

## 3. 文档同步

- [ ] 治理文档已同步（目录结构 / 模块图 / 配置 / 引擎契约）：对外可见部分写入 `docs/CODING_STANDARDS.md` §5 与本文；`AGENTS.md` 侧同步由维护者本地完成
- [ ] 新增模块在模块图 `docs/project/MODULE_MAP.md` 有对应条目（该文件已随仓库分发）
- [ ] 踩坑已追加到 `docs/project/KNOWN_ISSUES.md`（触发/现象/做法/日期；同为本地账本，外部贡献者请在 PR 中描述）
- [ ] `CHANGELOG.md` 已记录变更（type 对应 Added/Fixed/…）
- [ ] `python scripts/check_spec_refs.py` 退出码 0（无幻影/死链/假门禁）
- [ ] `python scripts/check_local_only_refs.py --all` 退出码 0（追踪文件不指向未随仓库分发的本地文件）

## 4. 国际化

- [ ] 新增 UI 文案已在 5 语言 JSON 同步（`app/integrated_app/locales/` 下 zh / zh-TW / en / ja / fr）
- [ ] 前端 spec 覆盖 i18n 切换（如涉及）

## 5. 构建 & 验证

- [ ] 完整构建/启动通过（`app/clean_launch.py`；`checkpoint.py` / `spec.py` 契约不破坏）
- [ ] 手动验证功能按预期工作（不仅是测试通过）
- [ ] 涉及便携包/发布时遵守 `docs/release-governance.md` 与 `docs/CODING_STANDARDS.md` §5.2 门禁口径（分卷便携包细则在维护者本地的 `docs/project/PORTABLE_BUNDLES.md`）

## 6. 安全 & 隐私

- [ ] 无硬编码密钥 / API Key / 敏感常量（走 `.env.example`）
- [ ] 新增路由遵守安全中间件（basic_auth / csrf / rate_limit / path_guard / magic_check / magic / watermark）
- [ ] 未新增静默吞错（`except: pass` 等）
- [ ] 涉及禁区目录（model/ 权重、integrity 签名、安全模块）走人工确认流程

## 7. 可追溯性

- [ ] 变更有影响评估（破坏性 / 非破坏性）
- [ ] 涉及配置变更时：`config.yaml` 结构同步 config_models + `check_config_refs.py`
- [ ] 涉及版本：`version.py` / `pyproject.toml` 一致
- [ ] 涉及 CI 变更时与 `.github/workflows/*.yml` 实际文件一致（证据绑定）
