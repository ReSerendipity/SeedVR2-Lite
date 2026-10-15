# 发布 / 回滚 / SLA 规范（SeedVR2-Lite）

> **来源与权威**：公共条款见 `ReSerendipity/.github` 仓库 `REPOSITORY_GUIDELINES.md` §10（发布 / 分发统一规范）。
> 本文件是本仓本地化副本：公共条款（远端优先、tag 驱动、版本位一致、不可变标签、扫描阻断、无 GPG）与 §10 保持一致，不自行改写；
> 下文只列本仓特有项。
> **收敛依据**：`RELEASE_GOVERNANCE_TEMPLATE.md`（`.github` 仓根目录）。

---

## 0. 远端优先（根原则，不可改写）

发布链的构建、打包、扫描、上传、推送**全部在 GitHub Actions 远端完成**；
本地只做三件事：改版本位、落 CHANGELOG、`git tag` + `git push`。
本地不执行打包 / 镜像构建 / 发布上传。

## 1. 版本号规范

- 遵循 SemVer `MAJOR.MINOR.PATCH`；预发布用 `-alpha.N` / `-beta.N` / `-rc.N`（公共条款，§10.2）。
- **唯一权威版本位**：`pyproject.toml` 顶层 `version`（当前 1.6.1）。
- 必须同步的其它版本位：
  - `app/integrated_app/version.py`：运行时直读 `pyproject.toml`，不另存版本串。
  - `CHANGELOG.md`：每个发版小节标题含版本号。
  - `version.json`：桌面壳更新器元数据（含 `minimum_shell_version`），随发布由 workflow 生成。
  - 维护者本地 `AGENTS.md` 顶部「对应项目版本」（不入库，外部贡献者忽略）。
- **漂移判定**：`python scripts/check_release_state.py` 钉住 `pyproject.toml` / `CHANGELOG.md` / 远端最新 tag 三者一致——漂一处即判红。
  某版本进了 CHANGELOG / pyproject 但暂不发，其 `## [X.Y.Z]` 标题必须标「未发版」（v1.5.8 曾漏标挂 9 天无人报警）。
- 自动化方式：手动 tag（本仓未引入 release-please）。

## 2. CHANGELOG 管理

- 遵循 Keep a Changelog：`Added / Changed / Deprecated / Removed / Fixed / Security`（公共条款，§10.2）。
- 每次合并 `main` 在 `[Unreleased]` 追加；type 映射 `feat→Added`、`fix→Fixed`、`refactor→Changed`。
- 发版时 `[Unreleased]` → `[VERSION] - YYYY-MM-DD`。

## 3. 发布流程（远端）

1. 确认 `[Unreleased]` 条目完整。
2. 同步版本位（见 §1 清单）。
3. 本地打 tag 并推送：`git tag v<X.Y.Z>` → `git push origin v<X.Y.Z>`。
4. 远端凭 tag 触发以下发布 workflow：
   - **`portable-release.yml`**：构建 Windows x64 离线分卷便携包 → 解包冒烟 → 产物保留为 workflow artifact；
     发布到 Release 需 `workflow_dispatch` + `upload_to_release=true`（tag 推送默认只构建不发 Release，避免覆盖增量更新包）。
   - **`docker-publish.yml`**：构建并推送 ghcr.io 容器镜像。
5. 远端建 draft Release 并挂资产 → 人工审核后 `published`。
6. 崩溃预算核验（X2，2026-10-15 新增）：发版前在常测机器跑 `python scripts/check_crash_budget.py`（默认 7 天窗口 ≤5 次，崩溃记录见 `logs/crashes/`）；超限先复盘再发版。
7. `gh run list` 盯到终态；红了当场修或 revert 止损。

## 4. 分发产物与完整性

| 产物 | 构建位置 | 标签 / 命名 | 完整性手段 |
|---|---|---|---|
| Windows x64 离线分卷便携包（core / torch / model-shared / model-fp8） | 远端（`portable-release.yml`） | GitHub Release 资产，不可变（已发布资产不覆盖） | `SHA256SUMS.txt` + CycloneDX SBOM（`sbom-python.cdx.json`）+ SLSA build provenance + manifest Ed25519 签名 |
| 容器镜像 `ghcr.io/ReSerendipity/seedvr2-lite` | 远端（`docker-publish.yml`） | semver（`{{version}}` / `{{major}}.{{minor}}` / `{{major}}`）+ 不可变 `sha-<long>`；**无 floating `latest`** | `provenance: true` + `sbom: true` |

- 容器镜像：推 `ghcr.io`，`packages: write`；镜像引用全小写；镜像内不含模型权重（`.dockerignore` 排除 `model/`），运行时挂载。
- 镜像发布前须经 Trivy `CRITICAL,HIGH`（`ignore-unfixed`）**阻断**；豁免逐条登记在 `.trivyignore.yaml`（理由 + expiration）。
  **本仓现状（2026-10-18）**：已转阻断——`docker-publish.yml` 的 `exit-code: 0 → 1`。2026-10-14 实测该镜像在门禁口径下零发现（无 CVE 行、无 Total 行），故当前无豁免条目；日后出现有修复版的 CRITICAL/HIGH 时门禁自动变红。
- **不使用 GPG 分离签名**（2026-10-18 裁决，§10.1 / §10.3）。完整性由 `SHA256SUMS.txt`（防传输损坏）+ Ed25519 manifest 签名（防伪造）承担。
- 发布后可复核记录落 `docs/execution/RELEASE_VERIFICATION.md`（§10.5）。

## 5. 回滚

- **判定阈值**（满足任一即触发）：
  1. 关键成功率指标跌破阈值；
  2. readiness 持续 degraded（模型加载 / 显存熔断）；
  3. P0/P1 安全告警（权重完整性校验失败且阻断启动）；
  4. 关键 API 契约断裂流入生产；
  5. 崩溃预算超限：7 天窗口 >5 次崩溃（`python scripts/check_crash_budget.py`，记录在 `logs/crashes/`）。
- **执行**：`git revert <bad-release-commit>`（保历史、可再 forward）；
  配置回滚 `git revert` 对应 `config.yaml`；
  权重回滚：`model/` 禁区，SHA-256 复验（`integrity_manifest.json`）后再动，永不在运行时改权重；
  便携包回滚：旧版 Release 资产不可变，用户重下上一版 + 校验和复验。
- **破坏性动作**：重指向 / 删除 tag、移动任何远端 ref 须仓库所有者单独授权（§10.2）。
- **回滚后验证**：
  1. liveness `GET /api/system/ping` 返回 ok；
  2. readiness `GET /api/system/ready` 返回 ready；
  3. 最小 smoke：`pytest` smoke 用例 / Playwright 冒烟；
  4. 关键指标回升、无新告警（`GET /metrics`）。

## 6. SLA / 错误预算

- 目标可用性 ≥99.5%（月度）⇒ 错误预算 ≈3.6h/月。
- 超出错误预算：冻结非紧急发布、优先稳定项、复盘。
- liveness = 内存级探针；readiness = 深度探针（模型预热 503 + Retry-After）。

## 7. 部署目标

- **GitHub Pages**（`pages-deploy.yml`）：VitePress 文档站 + demo 站，推 `main` 时自动部署。
- **ghcr.io 容器镜像**（`docker-publish.yml`）：linux/amd64，供自托管部署引用。
- **GitHub Release 便携包**（`portable-release.yml`）：Windows x64 离线包，用户本机运行（属本地运行边界，§10.4）。
- **Kubernetes 自托管清单**：`deploy/kubernetes/`（deployment / service / pvc / servicemonitor 示例），由使用者在自有集群部署。

## 8. 发布前检查清单

- [ ] 版本位全部同步（见 §1 清单）
- [ ] CHANGELOG `[Unreleased]` 已改版本 + 日期
- [ ] 全量 `pytest` 通过（质量 gate 双 OS + E2E 无 `--update-snapshots`）
- [ ] `ruff` / `black` / `mypy` 全绿
- [ ] `python scripts/check_spec_refs.py` 退出码 0
- [ ] `python scripts/check_local_only_refs.py --all` 退出码 0
- [ ] `python scripts/check_release_state.py` 退出码 0
- [ ] 崩溃预算 `python scripts/check_crash_budget.py` 通过
- [ ] 便携包自测 `test_portable_bundle.ps1` 通过
- [ ] **本地未产出任何发布交付物**（全部远端构建）
