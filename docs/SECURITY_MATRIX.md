# SeedVR2-Turbo 安全矩阵

> 本文档列出项目的安全控制措施，供安全审计和合规检查参考。

## 身份与访问控制

| 控制项 | 实现方式 | 状态 |
|---|---|---|
| 分支保护 | GitHub Branch Protection：required checks（quality-gate ubuntu/windows + Typecheck mypy ratchet）+ 非 admin 须 1 审批；admin（ReSerendipity）直推豁免（`enforce_admins=false`，2026-09-28 起） | ✅ |
| DCO 签名 | .githooks/commit-msg 强制 Signed-off-by | ✅ |
| 代码审查 | PR 需 review | ⚠️ CODEOWNERS 已配置（.github/CODEOWNERS，死规则已于 2026-10-05 修正）；分支保护 require_code_owner_reviews=false，属策略声明而非强制 |

## 代码安全

| 控制项 | 实现方式 | 状态 |
|---|---|---|
| 密钥扫描 | gitleaks（pre-commit + CI） | ✅ |
| 依赖漏洞扫描 | pip-audit / Dependabot | ✅ |
| SAST | CodeQL / Semgrep | ✅ |
| 静态分析 | ruff / black / mypy（pre-commit + ci.yml） | ✅ |
| 完整性校验 | Ed25519 签名清单 | ✅ |

## 数据安全

| 控制项 | 实现方式 | 状态 |
|---|---|---|
| 敏感配置不入库 | .env.example 模板，真实配置 gitignore | ✅ |
| 路径遍历防护 | path_guard / CSRF middleware | ✅ |
| 输入校验 | 参数验证 / schema 校验 | ✅ |

## 发布安全

| 控制项 | 实现方式 | 状态 |
|---|---|---|
| 制品校验 | `SHA256SUMS.txt` + Ed25519 签名完整性清单（`integrity_manifest.json`）+ SLSA 构建出处证明 | ✅ |
| GPG 分离签名 | 已退出发布链路（2026-10-18，PR #164 删除 `gpg-signed-release.yml` 与 `sign-release` job；v1.6.1 起 Release 不再附带 `SHA256SUMS.gpg`） | — |
| 制品不可变 | Immutable Release Artifacts | ✅ |

---
*最后更新：2026-10-21*
