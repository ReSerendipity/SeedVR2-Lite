# 编码与 Git 规范（Coding & Git Standards）

> 本文件是仓库内所有入库内容的**可移植性、依赖与 Git 卫生**约束。任何修改请在提交前通读并对照检查。
> 最后更新：2026-09-20（新增「发布版号口径」门禁：README 声明版号须等于最新稳定 tag）。原 2026-09-18：路径可移植性专项修复 + 新增第 5 节「禁区与门禁口径」公开子集。

## 1. 路径可移植性（强制）

### 1.1 禁止

- 在入库文件中硬编码本机绝对路径：`C:\Users\<用户名>\...`、`/home/<用户名>/...`、`/Users/<用户名>/...`
- 在 pip 依赖清单里写 `file:///C:/...` 本机 wheel 路径
- 构建 / 打包 / 安装脚本里写死本机工具路径（如 `C:\Users\...\7za.exe`、`C:\Program Files (x86)\Microsoft Visual Studio\<版本>\...\vcvars64.bat`）

### 1.2 正确姿势

| 场景 | 做法 |
|---|---|
| Python 生产代码 | `Path(__file__).resolve().parents[N]` 推导项目根；外部路径用环境变量 + 项目内默认值 |
| PowerShell 脚本 | `$PSScriptRoot` 推导脚本所在目录 |
| 批处理 | `%~dp0` 推导脚本所在目录 |
| NSIS 安装器 | 相对脚本目录的路径 + `!ifndef/!define` 允许 `-D` 覆盖 |
| pip 依赖 | 官方索引 URL（如 `https://download.pytorch.org/whl/cu130/...`）或普通版本声明，禁止本机文件路径 |
| 数据 / 配置文件 | 不写本机路径；含路径的历史数据需在文档标注，不新增 |

### 1.3 本机专用（DEV-ONLY）运维脚本

仅本机使用的运维 / 开发辅助脚本（检查其他仓库 venv、清理本机数据等）：

- 文件头必须标注：`DEV-ONLY` + 用途 + 是否含本机路径
- 尽量参数化（环境变量 / 命令行参数）；不能参数化的必须明示"克隆后不可直接用"

### 1.4 提交前检查

PowerShell：

```powershell
# 全库搜索本机路径痕迹（排除文档与锁定文件后人工复核）
rg -n -i "C:\\Users\\|/home/|/Users/" --glob '!*.md' --glob '!*.lock' .
```

Python 项目：

```bash
grep -rn --include='*.py' -iE 'C:\\Users|/home/|/Users/' .
```

## 2. Git 工作流（强制）

- main 受保护，但 admin（ReSerendipity）直推豁免（`enforce_admins=false`，2026-09-28 治理变更）：本地预检全绿后可直接 `git push origin main`；非 admin 账号 push main 会被 GitHub 拒绝，须走功能分支 + PR（1 审批 + required checks 全绿）。提交信息使用 Conventional Commits 风格：`feat: / fix: / chore: / docs: / refactor: / test:`；同一仓库内语言保持一致。
- 钩子存放在 `.githooks/` 并随仓库分发（仓库自包含），克隆后启用：

  ```bash
  git config core.hooksPath .githooks
  ```

- `.gitattributes` 统一 `text eol=lf`；`.mailmap` 用于历史身份归并（新增身份先加映射）。
- 合并 PR 后删除源分支；恢复期保护分支（backup / pre-recovery-* 等）由所有者确认后删除，不擅自清理。

## 3. 仓库自包含（强制）

- 不依赖"家族 / 公用"仓库的内容：工作流、钩子、脚本必须随本仓库分发，保证**克隆后即可工作**。
- 已知例外（需逐步消除）：CI 引用 `ReSerendipity/.github` 的 self-purify.yml / python-quality-baseline.yml。新增工作流禁止再引用外部仓库文件。
- 不使用 git submodule / symlink 传递必要内容。

## 4. 卫生与安全

- 密钥、`.env*`、本地数据库 / 产物不入库（见 `.gitignore`）。
- 大文件（>10MB）不入库，已有大资产走 LFS 或移出仓库。
- 不提交生成物（build/、dist/、target/、__pycache__/、*.log）。
- 修改 `.gitattributes` / `.gitignore` / `.mailmap` 前先确认与上游 `origin/main` 一致，避免重复 / 冲突提交。

## 5. 禁区与门禁口径（公开子集）

> **为什么有这一节**：本仓库的完整 AI 协作协议 `AGENTS.md` 及其子文档 `docs/agents/`、贡献指南
> `CONTRIBUTING.md`、修复交接日志 `FIX_LOG.md`、本地预检脚本 `precheck.ps1` 按「干净交付」决策
> 只保留在维护者本机，**未随仓库分发**（见 `.gitignore`）。`docs/project/` 自 2026-10-20
> （`47a918e` 解除 docs/ 忽略）起部分文件已随仓库分发（ARCHITECTURE / CONSTRAINTS /
> FIRST_TIME_USER_GUIDE / MODULE_MAP / PROJECT_CONTEXT / model_algorithm_analysis），
> 其余（KNOWN_ISSUES / PORTABLE_BUNDLES / AI_DEV_SOPS）仍未跟踪、不随仓库分发。
> 本节把其中对外必须可执行的两块——**禁区声明**与**门禁口径**——摘成公开子集，使克隆后即可遵守，
> 不依赖任何未分发文件；需要更细的规则请在 Issue / PR 中向维护者询问。
> 冲突裁决顺序：代码与配置 > 本仓库追踪文件 > 本节摘录。

### 5.1 禁区（默认禁止自动修改，人工显式授权除外）

| 路径 | 原因 | 授权改动后仍需 |
|---|---|---|
| `model_lib/` | 上游 vendored 模型架构源码（DiT / DiT v2 / Video VAE）：mypy 整体排除（`pyproject.toml` 的 `exclude = "^model_lib/"`），ruff 仅对其放宽命名类规则（`[tool.ruff.lint.per-file-ignores]`），改错没有静态网兜住 | 说明改动理由与上游差异，登记到 `model_lib/SOURCE.md` |
| `app/integrated_app/security/` | 认证 / CSRF / PathGuard / 完整性清单与签名等安全边界 | 在 PR 中单列安全变更说明并补测试 |
| `config.yaml` | 运行时唯一配置源 | 同步 `app/integrated_app/config.py` 与 `app/integrated_app/config_models.py`，并跑 `python scripts/check_config_refs.py` |

### 5.2 门禁口径（合入前必须为绿的检查）

| 检查 | 命令 | 接线位置 |
|---|---|---|
| 静态 / 格式 / 类型 | `ruff check .`、`black --check .`、`mypy app/integrated_app` | `.pre-commit-config.yaml`、`ci.yml` |
| 单测与覆盖率 | `pytest tests/ --cov=app/integrated_app -q`（阈值 55%：`pyproject.toml` 的 `fail_under` 与 `ci.yml` 的 Coverage Gate 同值） | `ci.yml` |
| 前后端契约 | `python -m pytest tests/test_api_contract.py -q` | `ci.yml` |
| API 一致性 | `python scripts/audit_api_consistency.py all` | `ci.yml` |
| 端到端 UI（3 浏览器矩阵） | `cd tests && npx playwright test --project=chromium-desktop`（CI 另跑 firefox / webkit；本地需先起 `app_server.py`） | `e2e.yml` —— **main 必需检查**；PR 触发刻意不带 `paths` 过滤，被过滤掉的 workflow 不产生 check run，必需检查会永远停在 Expected |
| 根目录结构 | `python .github/scripts/check_layout.py` | `structure-guard.yml`、pre-commit |
| 服务层边界 | `python scripts/check_service_layer.py`（`services/` 不得 import Web 框架的请求/响应对象，口径见 `app/integrated_app/services/__init__.py`） | `ci.yml`、pre-commit |
| 路径可移植性 | `python scripts/check_no_hardcoded_paths.py --all` | `.githooks/pre-commit` |
| 引用可用性 | `python scripts/check_local_only_refs.py --all` | `structure-guard.yml`、`docs-consistency.yml`、`.githooks/pre-commit` |
| 规范引用幻影 | `python scripts/check_spec_refs.py`（依赖仓外家族 auditor，缺失时自动 skip） | `docs-consistency.yml`、`structure-guard.yml` |
| 发布版号口径 | `python scripts/check_readme_release_version.py`（README 声明须等于最新稳定 tag；浅克隆会硬失败） | `docs-consistency.yml` |
| 发布状态口径 | `python scripts/check_release_state.py`（`pyproject` 版本与 CHANGELOG 小节须对上最新稳定 tag；版本超前时 CHANGELOG 必须标「未发版」；无 tag 时硬失败） | `docs-consistency.yml` |
| 密钥扫描 | `gitleaks detect --config gitleaks.toml` | `gitleaks.yml`、`.githooks/pre-push` |
| 崩溃预算（X2） | `python scripts/check_crash_budget.py`（默认 7 天窗口 ≤5 次；崩溃数据只在本机，CI 不可代跑，发版前人工执行） | `docs/release-governance.md` §3、`docs/发布检查清单.md` |

- **push 前本地预检**：钩子目录 `.githooks/` 随仓库分发，克隆后执行 `git config core.hooksPath .githooks`
  启用；`pre-push` 在缺少维护者本地 `precheck.ps1` 时自动退回根目录守卫（提示而非失败）。此时不要把剩下
  的检查读成「CI 会兜底」：全部工作流的 `push` 触发都限定 `main` / `master`（`portable-release` 只认 `v*` 标签），
  **推功能分支不产生任何 check run**，CI 真正接手是在「开向 main 的 PR」或「推 main」那一刻；功能分支上的机器
  把关只剩已激活的本地钩子与 `scripts/check_local.py`（该激活状态可由 `check_local.py` 自检，见其钩子激活检查）。
- **门禁失败请改代码，不要用 `--no-verify` 绕过**；确需紧急绕过必须由仓库所有者明确授权，并在同一 PR 内说明原因。
- **CI 通过才算完成**：push ≠ 完成，需盯到终态；`main` 变红且 30-60 分钟内无根因时先 `git revert` 止损。
- **证据绑定**：文档里出现的可执行路径必须真实存在且同样已分发；追踪文件确需提到未分发的本地文件时，
  就地补一句可获取性说明，或在文件内用「本地未分发引用：…」统一声明（`check_local_only_refs.py` 的口径）。

## 6. 本仓修复记录（2026-09-18）

- `app/integrated_app/data/checkpoints/cc576213d2d54c11.json`：历史数据含本机绝对路径（ComfyUI / outputs 元数据），**保留不改**（数据级历史样本）；新增 checkpoint / 数据文件禁止写入本机路径。
- 本次扫描未发现生产代码 / 构建脚本硬编码执行路径；后续按第 1 节规则执行即可。

## 7. 关联文档

- `docs/DOD.md`（完成定义）；`docs/COMPLIANCE_CHECKLIST.md`（对外服务合规检查清单，已随仓库分发）
- `docs/release-governance.md`（版本与发布）、`docs/ci/local_ref_baseline.json`（引用可用性基线）

---

## 8. Markdown 写作规范

所有 .md 文件遵循以下约定（2026-09-19 家族统一新增）：

- **编码**：UTF-8 **无 BOM**。禁止保存为带 BOM 的 UTF-8、UTF-16 或 GBK。
- **行尾**：统一 LF（.gitattributes 已锁 *.md text eol=lf），禁止 CRLF 入库。
- **行宽**：正文建议 ≤ 120 字符；URL、表格、代码块、长 JSON 不强制折行。
- **标题**：每个文件仅一个 #（一级标题），标题层级不跳级（# → ## → ###）。
- **中英文混排**：中文字符与英文/数字之间加一个半角空格（例：使用 ruff check .、Python 3.12）。
- **文件名**：公开文档优先英文 kebab-case（如 developer-guide.md）；存量中文文件名保留，新文档同一目录内风格保持一致。日期后缀统一用 -YYYYMMDD（如 exec-checklist-20260910.md）。
- **换行**：markdown 行尾两个空格表示硬换行；不要用 \ 转义。
- **链接**：相对路径引用仓库内文件，禁止引用本地绝对路径（C:\...、/home/...）。
