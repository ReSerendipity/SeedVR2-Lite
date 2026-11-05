# SRE 运行手册（SLA / SLO / 回滚 / 灾难恢复演练）

> **来源**：家族泛化自 TTS_MultiModel `docs/SRE_RUNBOOK.md` + 本仓既有云原生部署，本仓本地化。
> 配套：`docs/rollback_sop.md`、`docs/release-governance.md`。
> 适用版本：以 `pyproject.toml` 为准（当前 1.7.0，截至 2026-11-04）。

---

## 1. SLA / SLO

### 1.1 服务等级目标（SLO）

| 指标 | 目标 | 度量方式 | 数据源 |
|------|------|----------|--------|
| 可用性（月度） | ≥ 99.5% | liveness `/api/system/ping` | k8s `HEALTHCHECK` / `routes/system/health.py` |
| readiness（模型就绪） | ≥ 99.0% | `/api/system/ready`（模型预热中 503+Retry-After） | `routes/system/readiness.py` |
| P95 生成时延 | 设备相关 | 推理计时 | `metrics.py` |
| 生成成功率 | ≥ 99.0% | 成功/失败计数 | `/metrics` Prometheus |
| 完整性自检 | 100% | `integrity_check` 结果 | `security/integrity_check.py` |

### 1.2 错误预算（Error Budget）

- 月度可用性 99.5% ⇒ 允许不可用 ≈ **3.6 小时/月**。
- 超出时：冻结非紧急发布、优先稳定项、复盘。

### 1.3 探针（Probe）约定

- **liveness**：`/api/system/ping`（内存级，不碰 DB/GPU）。
- **readiness**：`/api/system/ready`（模型加载 + DB + GPU 深度检查）。

---

## 2. 回滚（Rollback）

> 详情见 `docs/rollback_sop.md`。

- **代码回滚**：`git revert <bad_commit>`，人工 push 后重启。
- **配置回滚**：`git revert` 对应 `config.yaml` 提交；容器只读挂载配置 + env 注入密钥。
- **权重回滚**：`model/` 禁区，SHA-256 复验（`integrity_manifest.json` / `integrity_check.py`）；永不在运行时改权重。

---

## 3. 灾难恢复演练（DR Drill）

| 场景 | RTO | RPO | 恢复手段 |
|------|-----|-----|----------|
| 单 Pod 崩溃 | < 1 min | 0 | k8s 自愈（liveness 重启） |
| 节点宕机 | < 5 min | 0 | 调度到其他节点（PDB） |
| 配置误改 | < 10 min | 0 | Git 回滚配置 |
| 权重损坏/篡改 | < 30 min | 按留存 | 从备份恢复 + 完整性复验 |
| history_db 损坏 | < 15 min | ≤ 留存窗口 | 从备份恢复 SQLite |
| 密钥泄露 | < 5 min | — | 轮转并重启 |

> 演练步骤（每季度一次）：预发副本部署 → 注入故障（kill Pod / 删 config / 损坏权重）→ 观测探针翻转 → §2 恢复 → 复盘记录 RTO/RPO。

**数据备份**：`history_db`（SQLite）定期备份 + 一致性快照 tips（无定制脚本时 `sqlite3 .backup`）；权重离线冷备 + `integrity_manifest` 复验；密钥走 `SEEDVR2_SECRET_KEY` / Secret 管理。

---

## 4. 事故复盘（Postmortem）

模板：时间线 → 影响面 → 根因（5 Whys）→ 改进项（owner+deadline）→ 是否触及错误预算。

---

## 5. 运维入口速查

| 用途 | 命令 / 端点 |
|------|-------------|
| 存活探针 | `GET /api/system/ping` |
| 就绪探针 | `GET /api/system/ready` |
| Prometheus 指标 | `GET /metrics` |
| 聚合诊断 | `python scripts/doctor.py`（八项诊断，`--json`） |
| 完整性缺签 | `python scripts/sign_integrity_manifest.py` |

## 日志与勘误（Log Locations）

| 用途 | 位置 |
|------|------|
| 应用主日志 | `logs/app.log` |
| 启动 / 根诊断 | `logs/_root_diag/`（启动自检与异常转储） |

> 勘误优先看 `logs/app.log` 尾部；启动失败再看 `logs/_root_diag/`。日志目录不入库（.gitignore 覆盖）。

## 治理与勘误速查

- **钩子**：`.githooks/pre-commit` 分发器四级回退（仓库 `.venv` → PATH python → 控制台 `pre-commit` → `pre-commit-lite`），细节见 `.githooks/README.md`。
- **分支**：`main` 受保护但 admin 直推豁免（`enforce_admins=false`，2026-09-28 治理变更；必查清单以 `gh api repos/<owner>/<repo>/branches/main/protection` 为准，**别照抄本文**——必查项会随门禁演进变动）→ 本地 admin 凭据预检全绿后可直推，非 admin 账号变更走功能分支 + PR；pre-push gitleaks 扫描待推送提交（0 泄露才放行）。
- **Python 安全注释**：bandit 豁免用纯 `# nosec`（`# nosec B603,B607` 只压 B607 不压 B603）；subprocess 一律列表参数。
- **CodeQL 豁免**：**别指望行内注释**。`# codeql[py/path-injection] ignore` 紧贴告警行上一行、以及放在行尾，两种放法在 PR #98 上都实测无效（告警只是按新行号重锚）。要收口只有两条路：① 把消毒改成查询认得的形态（字符集白名单 + 使用 `fullmatch` 匹配值这条也没能拿下 `Path.resolve()`）；② 在告警面 `PATCH code-scanning/alerts/<n>` 以 `dismissed_reason="false positive"` 关闭并写理由。
