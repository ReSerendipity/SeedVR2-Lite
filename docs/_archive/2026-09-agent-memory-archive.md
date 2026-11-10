# 2026-09 维护会话记忆归档（agent 工具目录清理前固化）

> 本文合并自 5 份位于 agent 工具目录（`.workbuddy-ai/`、`.zcode/`，均被 `.gitignore` 忽略）的会话记录，
> 原始文件随目录清理删除。只保留可执行事实（命令 / 路径 / 参数 / 阈值 / 判据 / 结论），已由既有文档
> 登记的内容不重复（清单见 §8）。日期标注 `（09-06）` 即 2026-09-06；「现状」一律指 2026-11-04 当日核对。
> 本地未分发引用：`.workbuddy-ai/memory/2026-09-06.md`、`.workbuddy-ai/memory/2026-09-07.md`、`.workbuddy-ai/memory/2026-09-10.md`、`.workbuddy-ai/memory/2026-09-13.md`、`.zcode/plans/plan-sess_96f7cf73-92b2-47c3-b9d0-fb3852c76f64.md`、`precheck.ps1`（均未入库；源文件已随 agent 目录清理删除，`precheck.ps1` 为维护者本地预检脚本）。

## 0. 来源

| 来源路径 | 日期 | 原始行数 | 本文保留了什么 |
|---|---|---|---|
| `.workbuddy-ai/memory/2026-09-06.md` | 2026-09-06 | 45 | Git 仓库损坏恢复 runbook、cherry-pick 等价性判据、备份与 `.gitignore` 增补 |
| `.workbuddy-ai/memory/2026-09-07.md` | 2026-09-07 | 45 | 冗余分支删除的双重确认、假分叉根因与 `packed-refs` 根治 |
| `.workbuddy-ai/memory/2026-09-10.md` | 2026-09-10 | 159 | 发布链权限修复、v1.5.6/v1.5.7 事故链、当日测试与门禁数值 |
| `.workbuddy-ai/memory/2026-09-13.md` | 2026-09-13 | 371 | 权重双命名落点、测试隔离 / flake 修复、结构守卫与 CRLF 判据、v1.5.8 定版 |
| `.zcode/plans/plan-sess_96f7cf73-92b2-47c3-b9d0-fb3852c76f64.md` | 目标发版日 2026-09-30 | 102 | v1.6.0 发版全流程 runbook（已执行）+ 与实际的差异 |

## 1. 已过时 / 与现状冲突（先列，避免误用）

| 条目 | 来源 | 现状（2026-11-04） |
|---|---|---|
| `nvidia-smi` 报 `Failed to initialize NVML`、`get_gpu_memory_info()` 返回 0 | 09-10 / 09-13 | 属当时 agent 沙箱限制，非仓库事实；查 GPU 仍以 torch 为准（§2） |
| nvfp4 回退不做显存门槛校验 | 09-10 | 已修：改为 fail-open 估算（§7.2）。`min_vram_nvfp4_gb=16` 仍保留为配置值（`config_models.py:154`），但不再是硬拒条件 |
| 发布资产含 SHA256SUMS **GPG 签名** | 09-10 | 已过时：2026-10-18 裁决取消 GPG 分离签名，改由 `SHA256SUMS.txt` + Ed25519 清单签名承担（`release-governance.md` §4） |
| v1.5.8 定版即视为发版 | 09-13 | 未发版：无 tag、无 Release；CHANGELOG `[1.5.8]` 标题现标注「未发版（原记 2026-09-13；条目随 1.6.0 一并发布）」 |
| 本地 `ahead=4 / 229 / 29 / 30`、远端引用卡在旧值 | 09-06 / 09-07 / 09-13 | 均已收敛；远端真值一律用 `git ls-remote --heads origin main` 取，不看本地 `origin/*` |
| `fix/font-on-demand` 冗余分支、「远端只剩 main」 | 09-07 | 该分支已删除且不存在；远端现已存在多条 dependabot / 功能分支 |
| `history_db.SCHEMA_VERSION = 4` | 09-13 | 已升到 5（`DATA_GOVERNANCE_ASSESSMENT.md` §1.1，2026-09-21 复核） |
| 所有验证命令走 `.venv\Scripts\python.exe` | 09-10 / 09-13 | 本机 `.venv` 现为指向外部 venv 目录的 junction，且目标不可用 → 这些命令当前**不可执行**；改用系统 `py -3.12`（Python 3.12.10） |
| 计划中的 `AGENTS.md` 头部 1.5.8→1.6.0、协议 2.0→2.1 | 计划 | `AGENTS.md` 与 `docs/agents/` 为 gitignored 本地治理文件，且已改版为契约 v2；不入库、不需提交 |

## 2. 环境事实

- 测试机：RTX 5070 Ti **Laptop**，显存 **11.94 GB**，系统内存 **31.7 GB**；PyTorch **2.13.0+cu132**，cuDNN **92000**（09-10）。`requirements-lock.txt` 现仍钉 `torch==2.13.0+cu132`（2026-11-04 核对）。
- 该机除本项目外还有约 **1–5 GB** 显存被其它进程占用且**波动**：`torch.cuda.mem_get_info()` 的 free 在 0–4 GB 之间跳（09-10）。→ 显存类判据必须用「空闲显存」，不能用总显存。
- 查 GPU 命令（绕开 `nvidia-smi`）：`.venv/Scripts/python.exe -c "import torch; print(torch.cuda.get_device_name(0))"`（09-13）。
- 仓库：远端 `https://github.com/ReSerendipity/SeedVR2-Lite.git`，主分支 `main`（09-13）。当前 HEAD = `8905374`，所在分支 `fix/desktop-shell-csrf-unify`，工作区仅 `docs/发布与分发-单文件安装包.md` 有未提交修改（2026-11-04 核对）。

## 3. Git 仓库损坏与恢复（09-06 / 09-07）

### 3.1 起因与二次损坏（09-06）

- 触发：`git stash push -u` 被 SIGTERM 打断 → `.git/refs/` 整个目录丢失、`.git/objects/pack/*.pack` 全部消失（只剩 `.idx`）。
- 二次损坏（人为失误）：未确认原因就跑 `git init --bare --template=.`，把 bare 骨架（HEAD/config/objects/refs）漏到工作区根，污染源码树（后已清理）。**教训：损坏现场先只读取证，再动任何 git 写命令。**

### 3.2 有效恢复路径（可直接复用）

1. 备份现场：把 `.git` 整体复制到仓库外（本次另建了源码树与原 `.git` 两份备份）。
2. 重建骨架：`mkdir -p .git/refs/{heads,tags,remotes/origin}` + `touch .git/logs/HEAD`。缺 `refs/` 时 git 直接报 `not a git repository`，这是判据。
3. 清死引用：`git update-ref -d refs/original/*`（filter-repo 遗留，指向不存在对象，会让 fetch 报 `did not send all necessary objects`）。
4. 拉回对象：`git fetch origin '+refs/heads/*:refs/remotes/origin/*'`。
5. **关键坑**：`git update-ref` 对已在 `packed-refs` 中的 ref **静默失效** → 必须手写文件 `printf '%s\n' <sha> > .git/refs/remotes/origin/main`。
6. 重建索引：`rm .git/index && git read-tree HEAD`（index 残留指向丢失 blob 时，diff/status 报 `unable to read <sha>`）。
7. 分批提交保住成果。

损失与结果：远端 `main(94dbfe8)` 之后的 **41 个未推送 commit 的历史元数据不可恢复**；文件内容全部保住，09-07 记录最终重新落为 5 个提交（`fc6474b`/`135dd25`/`abbd55c`/`d862700`/`33e4e8d`）并推送，远端 `main = 515112a`（09-07）。

### 3.3 cherry-pick 等价性判据（09-06 / 09-07）

判定分支是否真有未合并内容，不看 `git log` 条数，看 patch-id：

```bash
git show <commit> | git patch-id --stable
```

- `68f85db ≡ 0ecdd47` → `f69e63145bab40798a3bf23634a83c7dd41f7560`
- `f488380 ≡ f6a6508` → `04462b1b904da3a22eee248c3b56869ae46c0577`

两次命中即证明 `fix/font-on-demand` 的内容已以新 hash 进入 `main`；`git log main..f488380` 仍列出它们只是 hash 变了。删除前另做双重确认（两提交均为 `main` 祖先）。删除命令 `git push origin --delete fix/font-on-demand`；pre-push 钩子对删除自动放行（提示 `branch deletion - skip precheck`）。

### 3.4 「假分叉」根因与根治（09-07 / 09-13，已知问题 #82 复现）

- 09-07 现象：`git status` 报 `ahead=229`。真值 `git ls-remote` → 远端 `main=515112a`，本地 `origin/main` 卡在 `6907074`。
- 根因：`.git/refs/remotes/` 被清空后 git 回落读 `packed-refs`，其中 `refs/heads/main`（`8a29a09`）与 `refs/remotes/origin/main`（`6907074`）都是陈旧值。
- 根治两步：① 写 loose ref（`mkdir -p .git/refs/remotes/origin` + `printf '%s\n' <sha> > .git/refs/remotes/origin/main`）；② **同时改 `.git/packed-refs` 的两行**为远端真实值——只写 loose ref 会在被清理后复发（这正是 09-06 手改 ref 隔夜回退的原因）。
- 09-13 复发形态：`packed-refs` 中 `refs/heads/main=4b0b13df`、`refs/remotes/origin/main=40dc8e3`；`git fetch origin main` 每次都报 `[new branch] main -> origin/main`，但 `.git/refs/remotes/origin/` 目录根本不存在、`rev-parse` 也解析不到 → **该环境里 fetch 的引用写入被吞掉**（`remote.origin.fetch` 配置本身正确）。处置：`git update-ref -d refs/remotes/origin/main` → 手工 `mkdir -p` + `printf` 写入 `ls-remote` 取到的真值。
- 判据（可复用）：**`git fetch` 报成功 ≠ ref 真落盘**；凡涉及远端跟踪引用的判断，一律 `git ls-remote --heads origin <branch>`。

### 3.5 新增 `.gitignore` 规则（09-06 / 09-13）

- `data/model_hash_cache.json`：运行时缓存，含本机绝对路径，禁止提交（09-06；现 `.gitignore:289` 在位）。
- `*.bak.*`：备份文件（09-06；现 `.gitignore:290` 在位）。
- `data/*.db.bak-v*`（09-13）：`history_db._backup_before_migration` 生成 `{db_path}.bak-v{N}`，而既有 `*.bak`（要求结尾 `.bak`）与 `*.bak.*`（要求 `.bak.`）都不匹配 `.bak-v3` → 每次 schema 迁移都留一个未跟踪文件；已显式补规则 + 注释（现 `.gitignore:79` 在位）。
- `data/history.db.bak-v3` 属用户数据备份，**保留文件、只改忽略规则**，不删。

## 4. 测试

### 4.1 测试隔离漏了第二条 config 写盘路径（09-13，GOTCHAS #123）

- 现象：跑全量测试后 `git diff` 出现无关改动 `user_preferences.default_seed: 42 → -1`。
- 根因：写 `config.yaml` 有**两条**路径。`tests/conftest.py` 的 `test_app` fixture 只重定向 `settings_module.save_config`，漏掉 `optimization/webui_enhancement.py::SettingsPersistence.save`（`open(...,"w")` + `yaml.dump`，默认落项目根）。`routes/system/settings.py`、`routes/ui/parameters.py`、`app_server.py` 三处都走它；其 `UserPreferences.to_dict()` 用模型默认值把 `default_seed` 覆盖为 `-1`——即长期观测到的「default_seed 被还原」，此前误判为 pre-commit stash 往返所致。
- 修复：fixture 内 monkeypatch `SettingsPersistence.__init__` 的默认 `config_path` → tmp（现 `tests/conftest.py:94-106` 在位）。
- 验证：此前会写盘的 4 个测试文件 **81 passed**，且 `config.yaml` md5 不变。
- 判据（可复用）：新增任何「写 config.yaml」入口都要在 fixture 里一并重定向；**判定手法 = 跑完比对 config.yaml 的 md5**。

### 4.2 `compute_quality` 临时脚本残留污染 lint（09-13，GOTCHAS #124）

- 现象：仓库根出现 15 个 `tmp????????.py`（各 1538 B，内容为 `smoke_portable_bundle.compute_quality` 内嵌的 PSNR/SSIM 辅助脚本）→ `ruff check .` 报 60 errors、`black --check .` 报 15 files would be reformatted。
- 根因：`NamedTemporaryFile(suffix=".py", delete=False, dir=str(app_dir))`，而 `tests/test_gpu_quality_gate.py` 传的 `app_dir` 就是仓库根；清理只在 `finally: os.unlink()`，进程被强杀（agent 后台任务回收 / 超时）时不执行，且 `contextlib.suppress(OSError)` 会静默吞掉 Windows 偶发 `PermissionError`。
- 修复：改用默认系统临时目录（不传 `dir=app_dir`），现 `scripts/smoke_portable_bundle.py:396` 已无 `dir=`。
- **操作教训**：不要在测试运行期间手工删仓库根的 `tmp*.py`——正在执行的 `compute_quality` 会因辅助脚本被抽走而失败（本轮实测因此产生 4 例假红）。**假红判据：单文件复跑转绿 → 先怀疑自己并发的文件操作，而非代码回归。**

### 4.3 「存量失败」= 测试过时，不是代码缺陷（09-13，GOTCHAS #125）

1. `test_pinned_retention.py::test_migration_v2_adds_pinned_column`：断言写死 `get_schema_version() == 3`，但 `_MIGRATIONS` 链式推进（v2→v3 补 `pinned`→v4 补 `deleted_at`），`SCHEMA_VERSION` 已 = 4。修：断言改用 `SCHEMA_VERSION` 常量 + 补验 `{pinned, deleted_at} ⊆ 表列` 与落库 `user_version == SCHEMA_VERSION`。
2. `test_history_db.py::test_delete_record`：用例假设物理删除，但 v4 起 `delete_record(record_id, soft=True)` 默认软删；`get_records()` 显式 `deleted_at IS NULL` 隔离（回收站隔离），而 `get_record()` **故意不过滤**（供回收站恢复）。修测试而非改代码（照测试改会破坏回收站语义），拆成「默认软删 / `soft=False` 物理删 / 重复软删幂等」三例。
3. 顺带补真实缺口：v4 回收站此前**零测试覆盖**（`grep -rn "soft=\|deleted_at" tests/` 无命中）→ 新增 `TestRecycleBin` 4 例（restore / clear / `purge_deleted_records` 保留期语义 / 空列表 no-op）。
- 注意：`HistoryRecord` dataclass **没有** `deleted_at` 字段，回收站行为只能经 API 断言。

### 4.4 微秒级边界导致 CI 单例 flake（09-13，GOTCHAS #128）

- CI 失败：`test_purge_deleted_records_respects_keep_days` → `assert await db.purge_deleted_records(keep_days=0) == 1` 实得 `0`（本机同一提交全绿）。
- 根因：清理条件是**严格**的 `deleted_at < cutoff`；`delete_record` 写 `deleted_at = now()`，`purge_deleted_records(keep_days=0)` 算 `cutoff = now()` → 同一微秒内相等，`<` 不成立。CI runner 更快更易同刻命中。
- 修复（`95005c2`）：显式构造比较两侧时间——软删后用 `_execute_write` 把 `deleted_at` 回拨 **40 天**，再以 `keep_days=30` 清理（40 > 30 必被清）。验证：`TestRecycleBin` 连跑 10 次 0 失败。
- 机制实测：本平台 `datetime.now()` 有时间粒度——连续 20000 次调用中**15498 次（77.5%）与前一次取值完全相同**，即「相等」是常态而非巧合。
- 全仓审计（7 文件 / 19 处 `datetime.now()`、`time.time()`）：其余用法只有两类——显式大偏移（`now - 30*86400`、`now - 25*3600`）或「0 = 禁用」守卫，均安全，**未改任何代码**。
- 教训：不要用「两个 `now` 相减」构造边界；**别用进度条字符位置反推失败用例名**，直接读日志 `FAILED` 行（本次据位置误判成 `test_benchmark_archive`，实为 `test_history_db`）。

### 4.5 API 一致性审计的「孤儿端点」处置（09-13，GOTCHAS #126）

- 现象：`precheck -Full` 里 `audit_api_consistency.py all` 退出码 1——v4 软删除的回收站三端点（`GET /api/system/history/recycle`、`POST .../restore`、`DELETE .../purge`）随特性落地但从未在 `KNOWN_ORPHANS` 归档，判 `unclassified`。
- 处置原则：**先补真实消费者，再归档，不用标签掩盖缺口**（`no-consumer` 在非 strict 下放行，`--strict` 仍失败）。新增 `tests/test_api.py::TestHistoryRecycleAPI` 7 例（分页契约 / `page=0`→422 / 缺 `record_ids`→400 / 未知 id 恢复 0 条 / POST 与 DELETE 的 CSRF），再在注册表标 `api-surface` 并写明消费文件。审计退出码归 0。现状（2026-11-04）：`scripts/audit_api_consistency.py:98` 起三条回收站端点已在 `KNOWN_ORPHANS` 登记，引用 `TestHistoryRecycleAPI`。

### 4.6 完整性清单的「基准字节」判定（09-13）

- `grep -c $'\r'` 判断 CRLF **会误报**：`$'\r'` 展开成空模式 → 匹配每一行，得到的是「总行数」而非 CR 数。改用 Python 字节级 `count(b'\r')` 才确认 11 个受保护文件**全为 LF、CR 字节数 = 0**。
- 判据（可复用）：拿 HEAD 清单里**未被改动**文件的期望哈希，与 `sha256(git show HEAD:<path>)` 比对——11/11 匹配即证明基准是**仓库态（LF）字节**而非工作区字节（工作区此时可能是 CRLF）。
- 干净检出复核：`git worktree add <tmp> HEAD --detach` 后跑自检 → **11/11 passed、`manifest_signed=True`**（签名流程本身见 `增量更新发布手册.md` §8.3）。

### 4.7 当日门禁数值（历史记录，勿当现行阈值，阈值 55%）

- 09-10（定版 v1.5.6）：**1543 passed / 0 failed**、覆盖率 **65.01%**；ruff / black / mypy(112 文件) / API 契约 7 passed / API 审计退出码 0。
- 09-13（双命名修复）：**1577 passed**（另记 1573 passed / 6 failed / 1 skipped，含 4 例自伤）、**62.62%**；black 366 files unchanged、mypy 118 files。
- 09-13（清存量失败 → CI 修复 → 收尾）：**1585 → 1592 → 1594 → 1599 passed**、覆盖率 **62.85% → 62.91%**；仓库根 `tmp*.py` = 0、structure-guard 0 warning、7 个 workflow 全 success。

## 5. 发版

### 5.1 发布链权限：SLSA 失败会连带跳过 Release（09-10，GOTCHAS #112）

- 首次 tag 触发 `portable-release.yml`（run `34474190906`）：构建、解包冒烟、体积门禁全过，但 SLSA provenance 因缺 OIDC 权限失败 → **后续 Release 创建 / 资产上传被跳过**。
- 修复（`6ee30fa`）：在 `.github/workflows/portable-release.yml` **顶层 `permissions`** 增加 `id-token: write` 与 `attestations: write`。
- 复跑（手动 dispatch，run `34476410544`，version=1.5.6、upload_to_release=true）全绿：分卷构建、解包冒烟、SLSA attestation、Release 创建 + 10 个资产上传。现状：`id-token` / `attestations` 权限要求仍有效；同一表述里的「SHA256SUMS GPG 签名成功」已过时（§1）。

### 5.2 v1.5.6 误发便携包 → v1.5.7 增量补发（09-10）

- 事实：v1.5.6 的 Release 只含便携分卷、缺 `app-v1.5.6.zip`；已安装用户检查更新命中版本号却取不到更新包 → 以 v1.5.7 走增量通道补发，v1.5.6 改名为「⚠️ 误发布 · SeedVR2 v1.5.6 (portable offline bundles)」并在正文指向 v1.5.7。完整步骤与踩坑（`gh release create v*` 会推 tag → 触发便携构建，需 `gh run cancel`）见 `docs/增量更新发布手册.md` §9 与 §5 ⑥⑦，此处不重复。

### 5.3 v1.5.8：定版但未发版（09-13）

- 操作：CHANGELOG `[Unreleased]` → `[1.5.8] - 2026-09-13`；`uv version 1.5.8` 原子同步 `pyproject.toml` 与 `uv.lock` 内 `seedvr2-lite` 版本（`version.py` 以 pyproject 为准）；提交 `eb6f440`（3 文件）。
- 结果：**从未打 tag、从未建 Release**（现存 tag 从 `v1.5.7` 直接到 `v1.6.0`）；条目随后随 v1.6.0 发布。此后新增 `scripts/check_release_state.py` 专治这类「代码版本位超前于实际发布」，见 `release-governance.md` §1。

### 5.4 v1.6.0 发版 runbook（计划已执行，保留给下版）

计划输入（目标发版日 2026-09-30）：`pyproject.toml=1.6.0`、CHANGELOG `[1.6.0] 未发版`、最新 tag `v1.5.7`；三项决策——桌面完整分卷包（Tauri 壳 + WinPython 运行时 + torch + 内置 3B fp8）、浏览器端与装后桌面端各跑一次真实推理（12 GB 卡、512 分辨率小图、设时间预算，OOM/超时降级跳过并记录）、Release 资产 = 增量包 + 桌面分卷 + `SHA256SUMS` + manifest + 解压脚本。

阶段与命令（保留）：

| 阶段 | 关键动作 / 命令 |
|---|---|
| 0 基线 | `git status` 干净；端口 **7870** 空闲（占用先排查不擅杀进程） |
| 1 浏览器全流程 | 先备份 `config.yaml` + `data/history.db`（模拟会写 user_preferences 与历史库），结束恢复后验 `git diff` 干净；`python -X utf8 app/clean_launch.py`（后台）+ 轮询 `/api/system/ping`；覆盖首页 / 首次运行流 / `/restore` 单图与批量 / 真实推理 + SSE / 失败路径 / `/history`（含回收站）/ `/system-status` / `/settings`（5 语言 + About 版本号）/ 全局组件 / `/docs` OpenAPI；证据截图落 `dist/evidence/fullflow/`（gitignored） |
| 2 打包 | `cargo test --locked` → `cargo clippy --locked --all-targets -- -D warnings` → `cargo build --release`；staging 组装（`SeedVR2.exe`、`git ls-files` 口径的 app/、根 `start_portable.py`、`pyproject.toml`(1.6.0)、`version.json`、`model/` 用本地 3B fp8 免下 2.2 GB MXFP8）；`scripts/setup_winpython.py` + 离线装 torch 2.13.0 cu132（口径对齐 `build_portable_bundle.ps1`）；先 staging 冒烟再分卷；门禁 `scripts/verify_release_integrity.py --app-root staging\app`；`powershell -File scripts/release_tauri.ps1 -Version 1.6.0`（每卷 ≤1900 MB）；`python scripts/build_app_increment.py --version 1.6.0`；滚动清理只留本轮产物 |
| 3 安装模拟 | 分卷 + 解压脚本复制到**仓外**下载目录 → 跑 `unpack_desktop.ps1` 逐卷 SHA256 校验 → 核对 `SeedVR2.exe` / `app/runtime/python.exe` / `model/` → 双击冷启（16–48 s）→ 壳内 UI 走查（About=1.6.0）→ **装后实例再跑一次 512 真实超分**（发布物金标准） |
| 4 发版 | CHANGELOG 归并 `## [1.6.0] - 2026-09-30`；README 徽章/稳定版；`version.json` release_date 与 changelog；文档门禁 `check_spec_refs.py` + `check_local_only_refs.py --all` + `check_release_state.py`；`precheck.ps1 -Full` 全绿；`git push origin main v1.6.0`（同次推送保证 README=tag）；`gh run watch` 盯 quality-gate(×2) + Typecheck；`gh release create v1.6.0` + 按 upload-list 上传；核验 `/releases/latest`、资产清单、docker-publish 终态 |

实际结果（2026-11-04 以 `gh release view v1.6.0` 与 git tag 核对）：

- tag `v1.6.0` → 提交 `1fc236a3`（2026-09-30，`chore(release): v1.6.0 发版收口`）；Release 创建于 2026-09-30T14:13Z，**非 draft、非 prerelease**。
- 资产 9 项：`app-v1.6.0.zip`（1,471,889 B）+ `.sha256`（81 B）、`manifest.json`（2,872 B）、`portable_bundle_lib.ps1`（26,155 B）、`SeedVR2-Desktop-v1.6.0-win-x64.7z.001/.002/.003`（1,992,294,400 / 1,992,294,400 / 820,170,446 B）、`SHA256SUMS.txt`（416 B）、`unpack_desktop.ps1`（5,918 B）。
- 与计划的差异：① 分卷合计 **4.80 GB**（1900+1900+820 MB），低于计划预案里 8.5 GB payload 的预估；② 资产无 GPG 签名（计划仅列为「可选 dispatch `gpg-signed-release.yml`」，且 2026-10-18 已废止 GPG）；③ 计划约定的「全流程 UI 模拟」验证报告（存放于 `docs/reports/`）**未见于仓库**（该目录不存在，是否执行未核实）；④ 计划「tag 触发的 Portable Release 需取消」是否真的取消**未核实**；⑤ 计划中的 AGENTS.md 版本位同步属本地治理，未入库。阶段 1/3 的模拟结论本身未见留存证据（未核实）。

## 6. 工具与坑

- **PowerShell 无法 spawn 原生 `.exe`**（09-13，特定 agent 运行环境）：`& <任意 python.exe> -c "print('x')"` 无输出且 `$LASTEXITCODE` 保持为空；`.venv` 的 python 与托管 python 表现相同（故非 venv/路径问题）。`precheck.ps1` 用 `if ($LASTEXITCODE -ne 0)` → `$null -ne 0` 恒真 → **七步 809 ms–4 s 内全部假失败**。判据：**所有步骤 1 秒内同时失败 = 环境问题**（对照 ruff ≈1 s / 全量 pytest ≈2.5 min / audit ≈25 s）；先逐项跑等价检查并全绿，`--no-verify` 仅在用户明示授权后使用（规则已登记 `CODING_STANDARDS.md` §5）。
- **钩子输出乱码**（09-13）：`.git/hooks/pre-push` 的 `-File` 改为 `-Command "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; & './precheck.ps1' …"`，`sh -n` 校验通过。该文件在 `.git/hooks/`（不入库，仅本机生效）。
- **`.gitignore` 查询形式决定结果**（09-13，GOTCHAS #130）：git **只有当路径被判定为「目录」时**才应用该目录自身 `.gitignore` 的规则。`printf '.uv-cache\n' | git check-ignore --stdin` 无输出（判为文件路径，`.uv-cache/.gitignore` 的 `*` 不参与匹配 → 假 WARN）；带尾斜杠 `.uv-cache/` 才命中。修复：抽出 `build_ignore_query(repo, entries)`（目录补尾斜杠，结果再 `rstrip("/")` 归一化）。效果：52 checked / 28 ignored / 1 WARN → **51 checked / 29 ignored / 0 WARN**；新增 `tests/test_layout_guard.py` 5 例，其中一例**故意断言「不带斜杠确实识别不到」**以锁住成因。现状（2026-11-04）：helper 在位，守卫实跑 `68 root entries, 48 checked, 20 ignored, 0 warning`。
- **同一文件不要在同一消息里发两个并行 Edit**（09-13）：第二次写会被另一个写覆盖，提示 `Successfully edited` 但内容丢失（本轮 v1.84→v1.85 与 1.5.7→1.5.8 两处，只落了前者）。对同文件多处改动应串行或一次大块替换。
- **删除遗留物的判据**（09-13）：`sv2chk/`（9-09 遗留的 config / manifest / 公钥副本）删除前做两项断言——① 全仓 `grep -rn "sv2chk"`（排除 `.git/`、backups、`_archive/`、`.venv`）除笔记外无引用；② 4 个文件全部是**已在 git 跟踪**文件的副本（删除零数据损失）。护栏：断言文件名集合完全匹配且每个都在 `git ls-files` 中，否则拒绝删除（删除须用户批准）。

## 7. 决策与结论

### 7.1 权重双命名的适用范围（09-13）

- 只有 **fp16 / fp8** 真正存在两套命名（numz `seedvr2_ema_*` ↔ Comfy-Org `seedvr2_*`）；`int8_convrot` / `mxfp8` / `nvfp4` **仅 Comfy-Org 提供**，config 里本就登记 Comfy-Org 名，无需 `_alt`。
- 权威判据：`scripts/download_model.py:76-77` 注释（fp16/fp8 用 numz，三种量化精度用 Comfy-Org，按前缀自动路由来源）+ `docs/LICENSE_COMPLIANCE.md` §3.2（ModelScope API 实测枚举）。
- 别名机制是**精度无关**的（对任何精度的任一命名都能解析），所以「兼容各个精度」全覆盖；`_alt` 哈希只给 fp16/fp8。白名单不因别名放松：主哈希或 `_alt` 命中才放行，别名只放宽「找文件」。
- 第五处重复实现 `scripts/download_model.py::_is_comfy_org()` 与 `weight_names.is_comfy_org_name()` 是**有意例外**（脚本须在未装 app 依赖的环境独立跑首次下载，不能 import `app.*`），但功能耦合（脚本按前缀选源、加载器按同一约定解析文件名，漂移就会「下回来的文件加载器解析不到」）→ 用 `tests/test_weight_names.py::TestDownloadRoutingConsistency` 2 例钉死（12 个代表性文件名判断一致；config 登记的 15 个精度文件名都能推出 2 个等价命名）。

### 7.2 VAE 卡死与显存：结论已归档，仅补两点

- 根因（nvfp4 加载期反量化 6.31 GB 常驻、`min_vram_*` 回退不校验、`dit_cache_model` 让 DiT 在 VAE 阶段常驻、tile 按总显存推荐、WDDM 换页 250–700 倍慢）与 8 项修复的实测数字已全文归档在 `docs/_archive/VAE_STALL_DIAGNOSIS_20260910.md`，不重复。
- 补记一：该批修复交付时**只有静态 / 逻辑验证**（无 GPU 环境），真机回归由用户执行；09-13 用户实测 `POST /api/restore/` 正常出图，确认生效。
- 补记二：`min_vram_nvfp4_gb=16` 保留，但判定改为 **fail-open**——用 `estimate_vram_requirements(..., blocks_to_swap=num_blocks-4) * 1.15` 做下界（3B nvfp4 全量 BlockSwap 下界 ≈9.0 GB、门槛 ≈10.35 GB < 12 GB → 放行）；`total_vram_gb<=0` 或估算失败一律放行；回退失败信息拆成「配置但未下载」/「磁盘有文件但显存不足被排除」两类，避免 503 误导。

### 7.3 其他结论

- GroupNormAccumulator / TiledVAEHook 空转：已由 `docs/adr/0006-gaussian-blend-not-adopted.md` 定案（tile 融合由上游 raised-cosine 负责，两个配置键已于 2026-10-28 移除；两个类保留但标注「未接线」）。现状核对：`seedvr2_engine.py:1157` 注释即该裁决，`vae_tiled_enhance.py:489/1295` 已标未接线。
- 「存量失败不能默认接受」：必须先读实现判定是测试过时还是代码缺陷；本次两例均为测试过时（§4.3）。
- 审计类负面结论同样要记录：`datetime.now()` 那 7 个文件里看似可疑的 `=0` 参数都是安全写法，**以后别误改**（§4.4）；发布完整性上 GPG 已废止，`SHA256SUMS.txt` 防传输损坏 + Ed25519 清单签名防伪造（`release-governance.md` §4）。

## 8. 已由既有文档覆盖，不再重复

| 内容 | 已有权威文档 |
|---|---|
| VAE 卡死根因、tile 实测数字（8.34 s / 5.45 GB vs 1.79 GB）、WDDM 换页、8 项 P0/P1 修复与复现脚本 | `docs/_archive/VAE_STALL_DIAGNOSIS_20260910.md` |
| 增量包门禁三路径、`verify_release_integrity.py` 退出码、清单重签六步（仓库态字节） | `docs/增量更新发布手册.md` §2 / §8 |
| `gh release create v*` 触发便携构建、需取消 run 的坑与 `c8d5576` 修法 | `docs/增量更新发布手册.md` §5 ⑥⑦ / §9 |
| v1.5.7 实操（246/247 文件、1.44 MB、SHA256 `4bc05cef…1e126`） | `docs/增量更新发布手册.md` §9 |
| 权重双命名互认的用户侧结论（两套文件可直接放 `model/`） | `docs/LICENSE_COMPLIANCE.md` §3.2（2026-09-13 更新） |
| Inno Setup / NSIS 编译与 CI 坑（`InfoBeforeMsg`、`DiskName`、`Exec` ResultCode 等） | `docs/_archive/INSTALLER-LESSONS.md` |
| v1.5.1–v1.5.4 壳、增量更新换载、tauri.localhost 弹浏览器、NSIS 修复 | `docs/_archive/发布检查清单.md`、`docs/_archive/壳更新-下一轮改动清单.md` |
| Gaussian/GroupNorm 空转与不引入混合的决策 | `docs/adr/0006-gaussian-blend-not-adopted.md` |
| 门禁命令表、禁区、admin 直推豁免、`--no-verify` 纪律 | `docs/CODING_STANDARDS.md` §5、`docs/SECURITY_MATRIX.md` |
| 版本位权威、发布流程、回滚、不可变资产、无 GPG | `docs/release-governance.md` §0–§5 |
| 部署 / systemd / Nginx / Docker / 备份 / 监控命令 | `docs/plans/DEPLOYMENT.md` |
| 启动入口与路由现状 | `docs/project/PROJECT_CONTEXT.md`、`docs/project/MODULE_MAP.md` |

未搬入的过时内容：09-10 关于「`min_vram_*` 完全不被校验」的原始描述（已被 fail-open 取代，仅保留结论与阈值）、GPG 签名、`ahead` 计数类瞬时状态、`SCHEMA_VERSION=4`、v1.5.8 作为已发版、`.venv` 命令可执行性，均已在 §1 标注现状，不再作为现行口径引用。
