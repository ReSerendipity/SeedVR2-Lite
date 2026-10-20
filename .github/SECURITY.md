# Security Policy

## Supported Versions

安全修复适用于以下版本（与 `pyproject.toml` 的当前版本保持同步）：

| Version | Supported          |
|---------|--------------------|
| 1.5.x   | :white_check_mark: |
| < 1.5   | :x:                |

## Reporting a Vulnerability

我们非常重视 SeedVR2 的安全问题。如果您发现安全漏洞，请**不要**通过公开 Issue 报告，而是按照以下流程私下披露：

### 报告渠道

- **邮箱**：请发送邮件至 `security@reserendipity.dev`（加密通信优先）
- **GitHub Security Advisory**：推荐使用 GitHub 的 [私密漏洞报告功能](https://github.com/ReSerendipity/SeedVR2-Lite/security/advisories/new)
- **邮件主题**：`[SECURITY] SeedVR2 - <简短描述>`

### 报告内容

为帮助我们快速定位和修复问题，请在报告中包含：

1. **漏洞描述**：问题的清晰描述及其影响范围
2. **复现步骤**：详细的复现方法（最小化 PoC 优先）
3. **影响评估**：可能的攻击场景和受影响的用户范围
4. **环境信息**：操作系统、Python 版本、GPU 型号、SeedVR2 版本
5. **建议修复方案**（可选）

### 响应时间承诺

| 阶段 | 时间承诺 |
|------|----------|
| 确认收到报告 | 24 小时内 |
| 初步评估与分类 | 3 个工作日内 |
| 修复方案制定 | 7 天内（严重漏洞优先 48 小时内） |
| 修复版本发布 | 30 天内（严重漏洞 7 天内） |

## Disclosure Process

1. **私下报告**：漏洞通过上述渠道私下报告给维护团队
2. **确认与评估**：维护团队确认漏洞并评估严重程度
3. **修复开发**：在私有分支中开发修复方案
4. **修复发布**：发布修复版本，并在 Release Notes 中说明安全问题
5. **公开公告**：修复版本发布后，通过 GitHub Security Advisory 公开披露漏洞详情
6. **致谢**：在征得报告者同意后，在公告中致谢漏洞报告者

## Security Measures

SeedVR2 已实施以下安全措施：

### 模型与文件安全

- **模型权重 SHA256 校验**：加载前自动验证权重文件完整性
- **核心模块启动自检**：`integrity_manifest.json` 记录核心安全模块哈希，启动时自动比对
- **上传文件魔数校验**：防止伪装扩展名上传恶意文件
- **pickle 安全加载**：`.pt` checkpoint 优先使用 `weights_only=True`，回退时打印安全告警

### 路径与访问控制

- **PathGuard 白名单**：扫描 / 下载 / 目录浏览 / 资源管理器等全部用户可控路径端点统一受 `runtime.security.allowed_base_dirs` 白名单约束（默认 `outputs/`、`data/uploads/`、`data/checkpoints/`、`model/`）；`browse-dir` 空路径仅返回白名单根目录列表，不枚举盘符
- **路径遍历防护**：拒绝包含 `..` 的路径，使用 `realpath()` 解析符号链接后按 `is_relative_to` 语义复检（防兄弟目录前缀绕过）
- **速率限制**：上传接口 30 次/分钟
- **网络绑定**：默认仅绑定 `127.0.0.1`，不对外暴露

### 输出保护

- **内容溯源**：推理输出默认嵌入不可感知的来源标识（HMAC 签名载荷绑定任务 ID，可反查产生任务）；验证工具 `scripts/verify_watermark.py` 支持图像与视频（视频为采样帧验证）
- **可鉴定范围**：隐式水印只在**未再加工的原件**上可验。图像按目标格式选档（无损走 `alpha=0.5`；JPEG/WebP 走鲁棒档 `0.05×3`，并在落盘后重读产物验签）；视频为三通道等幅 + 重复码 3 的鲁棒档，H.264 生产参数 CRF18/23 实测 16/16 采样帧可验（量化基准见 `scripts/experiment_watermark_transcode.py`，CI 回归 `tests/test_watermark_transcode.py`；视频路径代价 PSNR ≈ 37.5dB，视觉透明）。**不抗**缩放、裁剪、旋转与二次重编码；鲁棒档对 JPEG 属**临界存活**（真实照片 / 平滑渐变 q90-q95 可验，细密纹理与均匀白噪声 q95 即失效，q80 以下全失效）。需确定性可验证时用 `SEEDVR2_WATERMARK_PROOF_MODE=1` 强制无损输出。
- **失败兜底**：嵌入或落盘复验失败时按 `runtime.security.watermark_on_failure` 处置（默认 `mark_metadata`：溯源记录写入 `data/provenance/<输出名>__<路径哈希>.provenance.json`，目录可用 `SEEDVR2_PROVENANCE_DIR` 覆写，并记 `WATERMARK_LOSS_DEGRADED` 审计事件，绝不静默；`block` 则删除已落盘产物并抛错）；视频合成后另做抽样验证，通过率 < 50% 同样标记
- **发布产物完整性**：GitHub Release 随包发布 `SHA256SUMS.txt` 校验和，GPG 分离签名由 `.github/workflows/gpg-signed-release.yml` 与 `portable-release.yml` 的 `sign-release` job 承担——**密钥已配置，但签名不是发版的默认步骤**：那条 job 只在手动 dispatch 且勾选 `upload_to_release` 时运行，且只认文件名恰为 `SHA256SUMS.txt` 的资产。实测 12 个 Release 中仅 v1.5.0 与 v1.5.6 带 `SHA256SUMS.gpg`。验证公钥随仓库发布于 `website/docs/guide/release-signing-key.asc`，用户可比对 `gpg --verify SHA256SUMS.gpg SHA256SUMS.txt`。装机侧没有任何路径自动验签。代价记录在案：校验和与产物同页下发，Release 被入侵时攻击者可一并改写，GPG 才是独立信任根。

### 依赖安全

- **依赖版本锁定**：`requirements-lock.txt` 固定所有依赖版本
- **哈希验证**：支持 `--require-hashes` 哈希验证
- **依赖审计 CI**：自动化依赖漏洞扫描 workflow

## ⚠️ Important Security Notes

### 网络绑定警告

SeedVR2 的 Web UI **默认仅绑定 `127.0.0.1`**。**严禁将 `server.host` 修改为 `0.0.0.0` 或公网 IP**。本应用默认不含用户认证与权限隔离机制，直接暴露到公网将导致：
- 任意第三方调用推理 API 占用 GPU 资源
- 通过上传接口投递恶意文件
- 下载 `outputs/` 与 `data/uploads/` 目录内容

**容器/编排部署强制鉴权（fail-closed）**：镜像内置 `SEEDVR2_DEPLOYMENT=container` 标记，容器内启动时若 Basic Auth 未生效将**拒绝启动**并输出修复指引。启用方式（任选其一）：
1. 注入环境变量 `SEEDVR2_AUTH_USERNAME` + `SEEDVR2_AUTH_PASSWORD`（推荐，优先级高于 config.yaml）；
2. 在 config.yaml 配置 `security.auth.enable=true` / `username` / `password`；
3. 端口映射严格限定回环（如 `127.0.0.1:7870:7870`）时，可设 `SEEDVR2_ALLOW_UNAUTHENTICATED=1` 显式豁免。

如需局域网共享，请在反向代理（Nginx/Caddy）后增加 Basic Auth，并启用 HTTPS。容器 / 编排部署参考随仓库分发的
`docker-compose.yml` 与 `deploy/kubernetes/`（更细的部署手册 `docs/plans/DEPLOYMENT.md` 为维护者本地文件，未随仓库分发）。

### 模型来源

- 所有模型权重请从官方可信来源下载（ByteDance-Seed HuggingFace 组织）
- 切勿加载来源不明的 `.safetensors`、`.pt`、`.bin` 文件

---

版权所有 © 2024-2026 ReSerendipity. 本安全政策遵循 Apache License 2.0。
