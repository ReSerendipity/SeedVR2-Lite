<div align="center">

# SeedVR2-Turbo

**基于 SeedVR2 扩散模型的视频与图像超分辨率修复工具箱 —— 独立运行、一键修复，无需 ComfyUI。**

A standalone video & image super-resolution toolkit powered by SeedVR2 diffusion models. One-click restoration via Web UI — no ComfyUI required.

[![CI](https://github.com/ReSerendipity/SeedVR2-Turbo/actions/workflows/ci.yml/badge.svg)](https://github.com/ReSerendipity/SeedVR2-Turbo/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/ReSerendipity/SeedVR2-Turbo?logo=github)](https://github.com/ReSerendipity/SeedVR2-Turbo/releases/latest)
[![Python](https://img.shields.io/badge/Python-3.12+-blue?logo=python&logoColor=white)](pyproject.toml)
[![CUDA](https://img.shields.io/badge/GPU-NVIDIA%20CUDA-76B900?logo=nvidia&logoColor=white)](#系统要求与兼容性)
[![License](https://img.shields.io/badge/License-Apache%202.0-green)](LICENSE)
[![Last commit](https://img.shields.io/github/last-commit/ReSerendipity/SeedVR2-Turbo)](https://github.com/ReSerendipity/SeedVR2-Turbo/commits/main)
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-brightgreen)](#-贡献与支持)

**[中文](#中文) · [English](#english)**

> 治理规范 / Governance：[ReSerendipity/.github/governance](https://github.com/ReSerendipity/.github/tree/main/governance)

</div>

---

## 中文

### 项目简介

SeedVR2-Turbo 把字节跳动 Seed 团队与南洋理工大学 S-Lab 联合开源的 [SeedVR2](https://huggingface.co/ByteDance-Seed) 单步扩散修复模型，包装成一个**独立运行的本地 Web 应用**：上传一张图片或一段视频，即可完成超分辨率与细节增强，数据全程不出本机。它不依赖 ComfyUI，也不需要任何外部推理服务。

对外正式分发为 **Tauri v2 Windows 桌面应用**。当前最新稳定版为 **v2.0.2**；安装器内含桌面应用及应用数据，推理运行环境与模型由用户首次启动后在「下载管理」中安装/下载。安装完成后可从桌面快捷方式或开始菜单启动。

### 核心特性

- **视频与图像修复**：基于单步扩散模型的超分辨率与细节增强，本地推理、数据不出机器。
- **三档模型**：3B / 7B / 7B-Sharp，支持 FP16 / FP8 及 MXFP8 / NVFP4 / INT8-convrot 量化变体（`.safetensors`，不兼容 GGUF）。
- **显存友好**：GPU Block Swap 在推理时动态换入换出 Transformer 块；3B FP8 可在 8 GB 显存起步运行。
- **批量与断点续跑**：单文件修复 + 文件夹批量扫描，批量任务在 `data/checkpoints/` 记录进度，中断后可续跑。
- **Windows 桌面应用**：Tauri 原生窗口、系统托盘、Toast 通知、文件拖拽、单实例与崩溃恢复；从桌面/开始菜单启动，首次运行在应用内安装推理环境和模型；应用内更新使用完整安装器，更新后自动重启并保留用户数据。
- **Agent CLI**：`seedvr2 status/check/models/launch/restore` 全局命令，JSON 输出 + 语义退出码，供脚本与 AI agent 自动化调用。
- **多语言与实时监控**：界面支持简体中文、繁体中文、英文、日文、法文；GPU/显存/内存与任务进度经 SSE 实时推送。

### 界面展示

> 以下截图均在云端 Linux（无 GPU）真实启动 Web UI 后截取，浅色主题；无 GPU 时界面如实显示降级状态。

**首页仪表盘** —— 一句话定位、入口卡片与状态角标。本图在无 GPU 云环境启动，角标显示「未检测到可用 GPU · 未加载」；有 GPU 时此处显示设备型号与当前模型。

![首页仪表盘](https://gcore.jsdelivr.net/gh/ReSerendipity/SeedVR2-Turbo@main/docs/assets/readme-seedvr2-turbo-1-home-20261026.png)

**修复工作台** —— 左侧拖拽上传图片/视频，右侧参数面板选择模型、输出分辨率与 VAE。无 GPU 时顶部提示「需要 NVIDIA CUDA GPU 才能执行修复」，界面与参数仍可完整查看。

![修复工作台](https://gcore.jsdelivr.net/gh/ReSerendipity/SeedVR2-Turbo@main/docs/assets/readme-seedvr2-turbo-2-workbench-20261026.png)

**高级参数** —— 展开 DiT 配置（最大分辨率、色彩校正、批处理大小），并可勾选「强制按参数重载模型」与一键「重置」。

![高级参数](https://gcore.jsdelivr.net/gh/ReSerendipity/SeedVR2-Turbo@main/docs/assets/readme-seedvr2-turbo-3-params-20261026.png)

**历史记录** —— 表格视图管理已完成任务，支持状态筛选与结果回看。

![历史记录](https://gcore.jsdelivr.net/gh/ReSerendipity/SeedVR2-Turbo@main/docs/assets/readme-seedvr2-turbo-4-history-20261026.png)

**系统状态** —— GPU/显存/利用率与内存监控。本图为无 GPU 云环境：GPU 卡片标记 UNAVAILABLE、显存字段为 `--`；真实 GPU 环境此处显示显卡型号、显存占用与利用率曲线。

![系统状态](https://gcore.jsdelivr.net/gh/ReSerendipity/SeedVR2-Turbo@main/docs/assets/readme-seedvr2-turbo-5-system-status-20261026.png)

**整体架构** —— 浏览器/桌面壳 → FastAPI 后端 → 扩散推理引擎（DiT + Video VAE + Block Swap），本地存储模型、历史与输出。

![架构图](https://gcore.jsdelivr.net/gh/ReSerendipity/SeedVR2-Turbo@main/docs/assets/readme-seedvr2-turbo-7-architecture.svg)

### 快速开始

> **普通用户无需克隆仓库、安装系统 Python 或运行启动脚本。** 当前最新稳定版为 **v2.0.2**：从 [GitHub Release 下载 Windows 安装包](https://github.com/ReSerendipity/SeedVR2-Turbo/releases/download/v2.0.2/SeedVR2-Setup-v2.0.2.exe)，并可用同批 [SHA-256 文件](https://github.com/ReSerendipity/SeedVR2-Turbo/releases/download/v2.0.2/SeedVR2-Setup-v2.0.2.exe.sha256)校验。

**1. 安装并启动桌面应用**

- 适用于 Windows 10/11 x64；图像/视频修复需要 NVIDIA CUDA GPU。Windows LTSC 等精简系统可能需要另行安装 WebView2 Runtime。
- 双击 `SeedVR2-Setup-v2.0.2.exe` 完成安装，然后从桌面快捷方式或开始菜单启动 **SeedVR2**。日常使用不需要打开终端或运行 `.bat` / Python 脚本。

**2. 首次使用时下载运行环境和模型**

安装包包含桌面应用及其应用数据，但**不包含完整推理运行环境或模型权重**。启动后打开「下载管理」，按页面引导安装 Python + PyTorch 推理环境、选择并下载模型；完成后按提示重启应用。请为运行环境和所选模型预留足够磁盘空间。

**3. 开始修复**

在「修复工作台」上传图片或视频、选择模型和输出参数后开始修复；任务进度与结果可在应用内查看。视频修复需另行安装 FFmpeg 并加入 `PATH`（因许可证原因不随安装包分发，见 [NOTICE](NOTICE)）。
#### 交付前检查（改动代码后）

上面的「最小验证」是需要 GPU 的人工冒烟；**改完代码要跑的机器检查**在下面（完整命令矩阵与覆盖率阈值口径见
[docs/CODING_STANDARDS.md](docs/CODING_STANDARDS.md) 第 5.2 节）：

```bash
git config core.hooksPath .githooks        # 克隆后执行一次，提交/推送即自动接上门禁
python -m pytest -q --ignore=tests/perf --cov=app/integrated_app --cov-report=term
python -m pytest tests/test_api_contract.py -q
ruff check . && black --check . && mypy app/integrated_app
python .github/scripts/check_layout.py
```

`pytest` 的覆盖率门槛为 55%（`pyproject.toml` 的 `fail_under` 与 CI Coverage Gate 同值）；
纯文档改动可只跑最后一条结构守卫。

### 系统要求与兼容性

| 项目 | 要求 |
|---|---|
| 操作系统 | Windows（推荐；源码/Linux 可经 Docker 运行） |
| GPU | NVIDIA CUDA GPU（必须） |
| Python | 桌面版无需单独安装系统 Python；推理运行环境由应用内「下载管理」安装 |
| 显存 | 3B FP8 最低 8 GB；3B FP16 约 16 GB；7B FP8 约 12 GB；7B FP16 约 24 GB |
| 磁盘 | ≥ 15 GB |

模型精度对照（`.safetensors`）：

| 模型 | 精度 | 最低显存 | 约内存 |
|---|---|---|---|
| SeedVR2-3B | FP16 | 16 GB | ~12 GB |
| SeedVR2-3B | FP8 | 8 GB | ~8 GB |
| SeedVR2-7B | FP8 | 12 GB | ~12 GB |
| SeedVR2-7B-Sharp | FP16 | 24 GB | ~20 GB |

> FP8 当前仅作权重存储格式，推理仍按 FP16/FP32 加载，故与 FP16 速度基本一致；真正影响速度的是 BlockSwap、输出分辨率与帧数。完整显存对比见仓内 [用户手册](docs/用户手册.md)。



### 使用示例

```bash
# Docker 本地构建运行
docker build -t seedvr2 .
docker run --gpus all -p 7870:7870 seedvr2

# 或使用随 v* 标签发布的 GHCR 镜像
docker pull ghcr.io/reserendipity/seedvr2-turbo:2.0.2
docker run --gpus all -p 7870:7870 ghcr.io/reserendipity/seedvr2-turbo:2.0.2
```

环境变量模板见 `.env.example`，常用 `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`。

### 常见问题（FAQ）

- **首次启动后无法修复？** 在应用内「下载管理」完成推理运行环境安装和模型下载，并按页面提示重启。
- **显存不足（OOM）？** 在工作台选择更轻量的模型/精度、开启 GPU BlockSwap 或降低输出分辨率；先使用应用内显存预检建议。
- **视频修复没有输出？** 单独安装 FFmpeg 并加入 `PATH`；图片修复不需要 FFmpeg。
- **自动更新失败？** 检查网络与磁盘空间后重试；也可从当前 [Release](https://github.com/ReSerendipity/SeedVR2-Turbo/releases/latest) 下载完整安装包覆盖安装。
- **模型下载慢？** 在「下载管理」切换可用下载线路后重试。
### Agent CLI（桌面版）

安装桌面版后，安装目录已加入当前用户 `PATH`；新开终端即可检查状态、配置和可用模型：

```bat
seedvr2 status --json
seedvr2 check --json
seedvr2 models --json
```

CLI 输出格式和退出码适合脚本及 AI agent 调用。图像/视频修复仍建议直接使用桌面应用中的「修复工作台」。
### 安全与合规

> **网络绑定警告**：Web UI 默认仅绑定 `127.0.0.1`（`config.yaml` 的 `server.host`），默认未开启认证。**严禁改为 `0.0.0.0` 或公网 IP**，否则任何人都可调用推理 API、上传恶意文件或下载 outputs/uploads。如需局域网共享，请开启 Basic Auth 并在反向代理后启用 HTTPS。

使用前请阅读 [USER_AGREEMENT.md](docs/USER_AGREEMENT.md)。本项目是独立第三方社区工具，与字节跳动及其 Seed 团队无隶属、赞助或官方合作关系，也与 seedvr2.com 等付费商业站点无关；模型权重仅从官方来源（Hugging Face `ByteDance-Seed/SeedVR2-3B` / `SeedVR2-7B`）获取。组件许可与归属清单见 [NOTICE](NOTICE)。

### 文档与治理

- **文档**：仓内 [用户手册](docs/用户手册.md) 与 [开发者指南](docs/开发者指南.md)。
- **治理规范**：组织级治理与贡献约定见 [ReSerendipity/.github/governance](https://github.com/ReSerendipity/.github/tree/main/governance) 与 [贡献指南](https://github.com/ReSerendipity/.github/blob/main/CONTRIBUTING.md)。
- **架构决策（ADR）**：关键技术选型记录于 [`docs/adr/`](docs/adr/)。
- **发布与更新**：桌面版通过完整安装器发布和更新；流程与回滚说明见 [release-governance](docs/release-governance.md) 和 [rollback_sop](docs/rollback_sop.md)，版本变更见 [CHANGELOG.md](CHANGELOG.md)。

### 贡献与支持

参与贡献请遵循上述[组织级贡献指南](https://github.com/ReSerendipity/.github/blob/main/CONTRIBUTING.md)（Conventional Commits + DCO 签名）。Issue 与 Discussion 请在仓库内发起；安全问题请按仓库安全政策私下报告，勿公开提 issue。

- Releases（最新桌面安装器与 SHA-256 校验文件）：<https://github.com/ReSerendipity/SeedVR2-Turbo/releases/latest>

### 许可证

本项目采用 [Apache License 2.0](LICENSE) 开源协议。Copyright 2024-2026 ReSerendipity。

---

## English

### Introduction

SeedVR2-Turbo packages the [SeedVR2](https://huggingface.co/ByteDance-Seed) one-step diffusion restoration model (jointly open-sourced by ByteDance's Seed team and NTU S-Lab) into a **standalone local web app**: upload an image or video and get super-resolution / detail enhancement on the spot, with all data staying on your machine. It does **not** depend on ComfyUI or any external inference service.

The supported distribution is a **Tauri v2 Windows desktop app**. The latest stable release is **v2.0.2**. The installer contains the desktop app and its application payload; users install the inference runtime and download model weights from the in-app Download Manager on first use.

### Highlights

- **Video & image restoration**: local super-resolution and detail enhancement via a one-step diffusion model; data never leaves the machine.
- **Three model tiers**: 3B / 7B / 7B-Sharp, with FP16 / FP8 and MXFP8 / NVFP4 / INT8-convrot quantized variants (`.safetensors`; GGUF is not supported).
- **VRAM friendly**: GPU Block Swap dynamically swaps Transformer blocks in/out during inference; 3B FP8 starts from 8 GB VRAM.
- **Batch & resumable**: single-file repair plus folder batch scanning, with progress persisted under `data/checkpoints/`.
- **Windows desktop app**: native Tauri window, system tray, Windows toast notifications, drag & drop, single-instance and crash recovery; launch from the desktop or Start menu, install the inference runtime and model in-app, and update through the full installer with automatic restart.
- **i18n & live monitoring**: UI in Simplified/Traditional Chinese, English, Japanese and French; GPU/VRAM/memory and task progress streamed over SSE.

### Screenshots

> All screenshots were taken by actually launching the web UI on a headless cloud Linux box without a GPU (light theme); the degraded no-GPU state is shown honestly.

**Home dashboard** — tagline, entry cards and status pill. This shot was taken on a GPU-less cloud box, so the pill reads "No GPU detected · model not loaded"; on a GPU box it shows the device name and current model.

![Home dashboard](https://gcore.jsdelivr.net/gh/ReSerendipity/SeedVR2-Turbo@main/docs/assets/readme-seedvr2-turbo-1-home-20261026.png)

**Restore workbench** — drag & drop upload on the left; model / output resolution / VAE panel on the right. With no GPU the top shows "NVIDIA CUDA GPU required to run restoration", yet the UI and parameters remain fully inspectable.

![Restore workbench](https://gcore.jsdelivr.net/gh/ReSerendipity/SeedVR2-Turbo@main/docs/assets/readme-seedvr2-turbo-2-workbench-20261026.png)

**Advanced parameters** — DiT config (max resolution, color correction, batch size), plus a "force reload model with parameters" checkbox and a one-click reset.

![Advanced parameters](https://gcore.jsdelivr.net/gh/ReSerendipity/SeedVR2-Turbo@main/docs/assets/readme-seedvr2-turbo-3-params-20261026.png)

**History** — table view of completed tasks with status filters and result review.

![History](https://gcore.jsdelivr.net/gh/ReSerendipity/SeedVR2-Turbo@main/docs/assets/readme-seedvr2-turbo-4-history-20261026.png)

**System status** — GPU/VRAM/utilization and memory monitoring. This is the GPU-less cloud state: the GPU card is marked UNAVAILABLE and VRAM fields read `--`; on a real GPU box it shows the card model, VRAM usage and utilization curves.

![System status](https://gcore.jsdelivr.net/gh/ReSerendipity/SeedVR2-Turbo@main/docs/assets/readme-seedvr2-turbo-5-system-status-20261026.png)

**Architecture** — browser/desktop shell → FastAPI backend → diffusion engine (DiT + Video VAE + Block Swap), with local model/history/output storage.

![Architecture](https://gcore.jsdelivr.net/gh/ReSerendipity/SeedVR2-Turbo@main/docs/assets/readme-seedvr2-turbo-7-architecture.svg)

### Quick start

> **Regular users do not need to clone the repository, install system Python, or run startup scripts.** The current stable release is **v2.0.2**: download the [Windows installer](https://github.com/ReSerendipity/SeedVR2-Turbo/releases/download/v2.0.2/SeedVR2-Setup-v2.0.2.exe); the matching [SHA-256 file](https://github.com/ReSerendipity/SeedVR2-Turbo/releases/download/v2.0.2/SeedVR2-Setup-v2.0.2.exe.sha256) is available for verification.

**1. Install and launch the desktop app**

- Windows 10/11 x64. An NVIDIA CUDA GPU is required for image/video restoration. Windows LTSC and other stripped-down editions may need WebView2 Runtime installed separately.
- Run `SeedVR2-Setup-v2.0.2.exe`, finish setup, then launch **SeedVR2** from the desktop shortcut or Start menu. Normal use does not require a terminal, `.bat` file, or Python command.

**2. Download the inference runtime and model on first use**

The installer includes the desktop app and its application payload, but **not the full inference runtime or model weights**. Open **Download Manager** in the app and follow its prompts to install the Python + PyTorch runtime and download a model. Restart the app when prompted, and allow sufficient disk space for the runtime and selected model.

**3. Restore your media**

In the workbench, upload an image or video, select a model and output settings, and start restoration. Progress and results are available in the app. Video restoration requires FFmpeg on `PATH` (not bundled for licensing reasons; see [NOTICE](NOTICE)).
### Requirements & compatibility

| Item | Requirement |
|---|---|
| OS | Windows (recommended; Linux via Docker from source) |
| GPU | NVIDIA CUDA GPU (required) |
| Python | No system Python installation is needed for the desktop app; install the inference runtime in the in-app Download Manager. |
| VRAM | 3B FP8 from 8 GB; 3B FP16 ~16 GB; 7B FP8 ~12 GB; 7B FP16 ~24 GB |
| Disk | ≥ 15 GB |

Model precision (`.safetensors`):

| Model | Precision | Min VRAM | RAM (approx.) |
|---|---|---|---|
| SeedVR2-3B | FP16 | 16 GB | ~12 GB |
| SeedVR2-3B | FP8 | 8 GB | ~8 GB |
| SeedVR2-7B | FP8 | 12 GB | ~12 GB |
| SeedVR2-7B-Sharp | FP16 | 24 GB | ~20 GB |

> FP8 is currently a weight storage format only; inference still runs at FP16/FP32, so speed is essentially the same as FP16. What actually affects speed is BlockSwap, output resolution and frame count. See the in-repo [user manual](docs/用户手册.md) for the full VRAM table.



### Usage examples

```bash
# Build & run locally with Docker
docker build -t seedvr2 .
docker run --gpus all -p 7870:7870 seedvr2

# Or use the GHCR image published with v* tags
docker pull ghcr.io/reserendipity/seedvr2-turbo:2.0.2
docker run --gpus all -p 7870:7870 ghcr.io/reserendipity/seedvr2-turbo:2.0.2
```

Environment variables: see `.env.example`; a common one is `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`.

### FAQ

- **Restoration is unavailable after first launch?** Install the inference runtime and download a model in the in-app Download Manager, then restart when prompted.
- **Out of memory (OOM)?** Select a lighter model/precision, enable GPU BlockSwap, or lower the output resolution; start with the app's VRAM preflight recommendations.
- **Videos produce no output?** Install FFmpeg separately and add it to `PATH`; image restoration does not require FFmpeg.
- **Automatic update fails?** Check network access and free disk space, then retry. You can also download the full installer from the [latest Release](https://github.com/ReSerendipity/SeedVR2-Turbo/releases/latest) and install over the existing app.
- **Model download is slow?** Choose another available download route in the Download Manager and retry.
### Desktop CLI

After installation, the app directory is added to the current user's `PATH`. Open a new terminal to inspect status, configuration and available models:

```bat
seedvr2 status --json
seedvr2 check --json
seedvr2 models --json
```

Structured output and exit codes are suitable for scripts and AI agents. For image/video restoration, use the app's workbench.
### Security & compliance

> **Network binding warning**: the web UI binds to `127.0.0.1` only (`server.host` in `config.yaml`) and ships without authentication by default. **Never change it to `0.0.0.0` or a public IP** — otherwise anyone can invoke the inference API, upload malicious files, or download outputs/uploads. For LAN sharing, enable Basic Auth and put the app behind an HTTPS reverse proxy.

Please read [USER_AGREEMENT.md](docs/USER_AGREEMENT.md) before use. This is an independent third-party community tool with no affiliation to ByteDance or its Seed team, and no relation to commercial sites such as seedvr2.com; weights are fetched only from official sources (Hugging Face `ByteDance-Seed/SeedVR2-3B` / `SeedVR2-7B`). Third-party licenses and attributions are listed in [NOTICE](NOTICE).

### Documentation & governance

- **Docs**: in-repo [user manual](docs/用户手册.md) and [developer guide](docs/开发者指南.md).
- **Governance**: org-level governance and contribution conventions live in [ReSerendipity/.github/governance](https://github.com/ReSerendipity/.github/tree/main/governance) and the [contributing guide](https://github.com/ReSerendipity/.github/blob/main/CONTRIBUTING.md).
- **Architecture decisions (ADR)**: key design records are under [`docs/adr/`](docs/adr/).
- **Release & updates**: the desktop app is released and updated through the full installer. See [release-governance](docs/release-governance.md) and [rollback_sop](docs/rollback_sop.md) for process and rollback guidance, and [CHANGELOG.md](CHANGELOG.md) for changes.

### Contributing & support

Follow the [org-level contributing guide](https://github.com/ReSerendipity/.github/blob/main/CONTRIBUTING.md) (Conventional Commits + DCO sign-off). Open issues and discussions in the repository; report security problems privately per the security policy rather than in a public issue.

- Releases (latest desktop installer and SHA-256 checksum): <https://github.com/ReSerendipity/SeedVR2-Turbo/releases/latest>

### License

Released under the [Apache License 2.0](LICENSE). Copyright 2024-2026 ReSerendipity.
