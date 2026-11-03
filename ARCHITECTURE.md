# 架构 — SeedVR2-Lite

> 治理定位：`delivery=release`、`lifecycle=active`（`governance/desired-state.json`）。
> 本文按 2026-11-03 的实际目录结构生成；数据流与边界须由维护者在实现变更时同步复核。

## 1. 定位

**基于 SeedVR2 扩散模型的视频与图像超分辨率修复工具箱 —— 独立运行、一键修复，无需 ComfyUI。**

## 2. 组件与依赖方向

```text
应用层：app/ (165)、desktop/ (100)、website/ (19)
服务层：portable_launcher/ (4)、launcher/ (2)
工具层：scripts/ (70)
资产层：model_lib/ (50)、model/ (1)
测试层：tests/ (186)、perf/ (9)
支撑层：docs/ (54)、.github/ (32)
其他层：common/ (27)、.githooks/ (10)、demo/ (10)、deploy/ (7)、data/ (6)、training/ (5)、experiments/ (2)、configs_3b/ (1)、configs_7b/ (1)、logs/ (1)
根级层：根级文件 (24)
```

| 组件 | 目录 | 文件数 | 代表文件 |
|---|---|---|---|
| `tests` | `tests/` | 186 | `tests/__init__.py`, `tests/capture-screenshots.js`, `tests/check-responsive.js` |
| `app` | `app/` | 165 | `app/__init__.py`, `app/clean_launch.py`, `app/integrated_app/__init__.py` |
| `desktop` | `desktop/` | 100 | `desktop/README.md`, `desktop/package-lock.json`, `desktop/package.json` |
| `scripts` | `scripts/` | 70 | `scripts/_ui_verify_server.py`, `scripts/agent_cli.py`, `scripts/audit_api_consistency.py` |
| `docs` | `docs/` | 54 | `docs/AI应用分发安全加固指南.md`, `docs/CODING_STANDARDS.md`, `docs/COMPLIANCE_CHECKLIST.md` |
| `model_lib` | `model_lib/` | 50 | `model_lib/SOURCE.md`, `model_lib/__init__.py`, `model_lib/common/__init__.py` |
| `common` | `common/` | 27 | `common/__init__.py`, `common/cache.py`, `common/config.py` |
| 根级文件 | `/` | 24 | `.dockerignore`, `.editorconfig`, `.env.example` |
| `website` | `website/` | 19 | `website/docs/.vitepress/config.mts`, `website/docs/guide/api.md`, `website/docs/guide/architecture.md` |
| `.githooks` | `.githooks/` | 10 | `.githooks/README.md`, `.githooks/commit-msg`, `.githooks/install.sh` |
| `demo` | `demo/` | 10 | `demo/README.md`, `demo/assets/inputs/input-1.jpg`, `demo/assets/inputs/input-2.jpg` |
| `perf` | `perf/` | 9 | `perf/__init__.py`, `perf/benchmark/__init__.py`, `perf/benchmark/bench_restore_api.py` |
| `deploy` | `deploy/` | 7 | `deploy/kubernetes/deployment.yaml`, `deploy/kubernetes/pdb.yaml`, `deploy/kubernetes/pvc-data.yaml` |
| `data` | `data/` | 6 | `data/test/image/transforms/area_resize.py`, `data/test/image/transforms/divisible_crop.py`, `data/test/image/transforms/na_resize.py` |
| `training` | `training/` | 5 | `training/__init__.py`, `training/dataset_manifest.py`, `training/distributed_trainer.py` |
| `portable_launcher` | `portable_launcher/` | 4 | `portable_launcher/README.md`, `portable_launcher/launcher.ps1`, `portable_launcher/launcher.sh` |
| `experiments` | `experiments/` | 2 | `experiments/_diag_ab.py`, `experiments/_diag_vae_bench.py` |
| `launcher` | `launcher/` | 2 | `launcher/release-notes-portable.md`, `launcher/requirements-small.txt` |
| `configs_3b` | `configs_3b/` | 1 | `configs_3b/config.json` |
| `configs_7b` | `configs_7b/` | 1 | `configs_7b/config.json` |
| `logs` | `logs/` | 1 | `logs/.gitkeep` |
| `model` | `model/` | 1 | `model/.gitkeep` |

依赖方向：入口层（应用 / 页面）调用领域层（业务模块），领域层依赖基础设施层（脚本、配置、模型资产）；反向引用不允许。

## 3. 数据流

上传视频 / 图像 → 推理管线修复 → 编码输出；`website/` 与 `portable_launcher/` 为分发面。

## 4. 外部依赖

| 依赖 | 用途 | 不可用时 |
|---|---|---|
| Python 依赖 | 推理管线 | 无法修复 |
| CUDA 运行时 | GPU 加速 | 退化为 CPU 或不可用 |

## 5. 边界与非目标

- 发布链只走 §10：远端优先、tag 驱动、资产附 `SHA256SUMS.txt`。
- 分发产物由 CI 生成，不入库。
- 不入库：密钥 / token / 口令 / 连接串、本机绝对路径、`local/` 与 `AGENTS.md`、构建产物。
- 治理正文只在 `ReSerendipity/.github/governance`，本仓不复制规则正文。

## 6. 变更影响面

| 改什么 | 必须同步 | 由谁校验 |
|---|---|---|
| 目录结构 / 入口 | 本文「组件与依赖方向」与「数据流」 | 维护者 |
| CI 与门禁 | 本仓 `README.md` 的测试命令；治理仓 §20 远端载体口径 | `audit-drift.py` |
| 依赖清单 | 锁文件与 `.github/dependabot.yml` 的生态段 | Dependabot / `lock-consistency` |
| README 展示图 | §8.1 真实性红线（图必须来自真实运行） | 治理仓 `readme-image-provenance` |
