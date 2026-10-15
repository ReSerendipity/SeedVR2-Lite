# 发布/回滚/SLA 总纲（Release Governance）

> **来源**：家族通用发布治理模板（泛化自 DraftPeek VERSIONING.md + TTS SRE_RUNBOOK/rollback_sop）；原家族文件
> `.spec_audit/family_release_governance.md` 已随家族归档、未随仓库分发，本文为本仓本地化副本。
> **适用范围**：SeedVR2-lite 全项目发布、回滚与运行稳定性。
> **本地未分发引用**：`AGENTS.md`、`docs/project/` 为维护者本地治理层，未随仓库分发；对外可执行的
> 禁区与门禁口径见 `docs/CODING_STANDARDS.md` 第 5 节。

---

## 1. 版本号规范

- 遵循 SemVer `MAJOR.MINOR.PATCH`。MAJOR=不兼容变更、MINOR=向后兼容新功能、PATCH=向后兼容修复。
- 版本权威位：`app/integrated_app/version.py`（直读 `pyproject.toml`）→ `pyproject.toml`，与 `CHANGELOG.md` 一致。
  **本文件不再手写「当前 vX.Y.Z」**——那个手写位点错过一次（写着 v1.5.8，而 v1.5.8 从未发版）。要问当前状态用机器：
  已发布看 `git tag --list 'v*' --sort=-v:refname | head -1`，代码备好未发布看 `python scripts/check_release_state.py`。
  （维护者本地的 `AGENTS.md` 顶部「对应项目版本」需同步，但该文件未随仓库分发，外部贡献者可忽略此步。）
- 可选预发布：`-alpha.N` / `-beta.N` / `-rc.N`。

## 2. CHANGELOG 管理

- 遵循 Keep a Changelog：`Added / Changed / Deprecated / Removed / Fixed / Security`。
- 每次 PR 合并 `main` 在 `[Unreleased]` 下追加，type 对应。

## 3. 发布流程（便携包为主产物）

1. 确认 `[Unreleased]` 条目完整
2. 同步版本位：`pyproject.toml` + `CHANGELOG.md`（维护者本地另有 `AGENTS.md` 一处）。**若某个版本进了 CHANGELOG / `pyproject` 但暂时不发，必须把它的 `## [X.Y.Z]` 标题标成「未发版」**——`scripts/check_release_state.py` 按这条口径硬校验（v1.5.8 曾以"有版本号有条目、无 tag 无 Release"的状态挂了 9 天无人报警）
3. 打 tag `git tag v<X.Y.Z>` → `git push origin v<X.Y.Z>` 触发 `portable-release.yml`（分卷便携包）
4. **签名不是发版自动步骤**：便携包随包发布 `SHA256SUMS.txt`；GPG 分离签名只在 `portable-release.yml` 以 `workflow_dispatch` + `upload_to_release=true` 运行时由 `sign-release` job 产出（或事后手动跑 `gpg-signed-release.yml`），且只认文件名恰为 `SHA256SUMS.txt` 的资产（细则见 `docs/project/PORTABLE_BUNDLES.md`，维护者本地账本）
5. CI 盯到终态：push 后 `gh run list` / `gh run watch`，红了当场修或 revert 止损
6. 崩溃预算核验（X2，2026-10-15 新增）：发版前在常测机器跑 `python scripts/check_crash_budget.py`（默认 7 天窗口 ≤5 次，崩溃记录见 `logs/crashes/`）；超限先复盘再发版

## 4. 回滚判定（满足任一即触发）

1. 关键成功率指标跌破阈值
2. readiness 持续 degraded（模型加载 / 显存熔断）
3. P0/P1 安全告警（权重完整性校验失败且阻断启动）
4. 关键 API 契约断裂流入生产

## 5. 回滚执行

- **代码回滚**：`git revert <bad_release_commit>`（保历史、可再 forward），人工 push 后重启。
- **配置回滚**：`git revert` 对应 `config.yaml` 提交；容器只读挂载配置 + 环境变量注入密钥。
- **权重回滚**：`model/` 禁区，SHA-256 复验（`integrity_manifest.json` / `weight_encryption`）后再动；永不在运行时改权重。
- **便携包回滚**：旧版 Release 资产不可变（`--clobber` 已去除），用户重新下载上一版本 + 校验和复验。

## 6. 回滚后验证（闭环）

1. liveness `GET /api/system/ping` 返回 ok
2. readiness `GET /api/system/ready` 返回 ready
3. 最小 smoke：`pytest` smoke 用例 / Playwright 冒烟
4. 观察关键指标回升、无新告警（`GET /metrics`）

## 7. SLA / 错误预算

- 目标可用性 ≥99.5%（月度）⇒ 错误预算 ≈ 3.6h/月。
- 超出错误预算：冻结非紧急发布、优先稳定项、复盘。
- liveness = 内存级探针；readiness = 深度探针（模型预热 503+Retry-After）。

## 8. 发布前检查清单

- [ ] 版本位全部同步（`pyproject.toml` / `version.py` + `CHANGELOG.md`；维护者本地 `AGENTS.md` 同步到位）
- [ ] CHANGELOG `[Unreleased]` 已改版本 + 日期
- [ ] 全量 pytest 通过（门禁实测：质量 gate 双 OS + E2E 无 `--update-snapshots`）
- [ ] `ruff` / `black` / `mypy` 全绿
- [ ] `python scripts/check_spec_refs.py` 退出码 0
- [ ] `python scripts/check_local_only_refs.py --all` 退出码 0（追踪文件不新增指向未分发文件的引用）
- [ ] `python scripts/check_release_state.py` 退出码 0（版本位、CHANGELOG 与已发布 tag 三者口径一致）
- [ ] 便携包自测 `test_portable_bundle.ps1` 通过
- [ ] SHA256 校验和已生成并抽查复算；需要 GPG 签名时**另行手动触发**并回看 Release 资产里确有 `SHA256SUMS.gpg`（`sign-release` 不在 tag 发布链路上，且绿色跳过与已签名在状态上同为 success——只有资产列表能区分）
- [ ] tag 已推送触发 `portable-release.yml`
