<div align="center">

# SeedVR2-Lite

**基于 SeedVR2 扩散模型的视频与图像超分辨率修复工具箱 —— 独立运行、一键修复，无需 ComfyUI。**

A standalone video & image super-resolution toolkit powered by SeedVR2 diffusion models. One-click restoration via Web UI — no ComfyUI required.

[![CI](https://github.com/ReSerendipity/SeedVR2-Lite/actions/workflows/ci.yml/badge.svg)](https://github.com/ReSerendipity/SeedVR2-Lite/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/ReSerendipity/SeedVR2-Lite?logo=github)](https://github.com/ReSerendipity/SeedVR2-Lite/releases/latest)
[![Python](https://img.shields.io/badge/Python-3.12+-blue?logo=python&logoColor=white)](pyproject.toml)
[![CUDA](https://img.shields.io/badge/GPU-NVIDIA%20CUDA-76B900?logo=nvidia&logoColor=white)](#系统要求与兼容性)
[![License](https://img.shields.io/badge/License-Apache%202.0-green)](LICENSE)
[![Last commit](https://img.shields.io/github/last-commit/ReSerendipity/SeedVR2-Lite)](https://github.com/ReSerendipity/SeedVR2-Lite/commits/main)
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-brightgreen)](#-贡献与支持)

**[中文](#中文) · [English](#english)**

> 治理规范 / Governance：[ReSerendipity/.github/governance](https://github.com/ReSerendipity/.github/tree/main/governance)

</div>

---

## 中文

### 项目简介

SeedVR2-Lite 把字节跳动 Seed 团队与南洋理工大学 S-Lab 联合开源的 [SeedVR2](https://huggingface.co/ByteDance-Seed) 单步扩散修复模型，包装成一个**独立运行的本地 Web 应用**：上传一张图片或一段视频，即可完成超分辨率与细节增强，数据全程不出本机。它不依赖 ComfyUI，也不需要任何外部推理服务。

对外正式分发为 **Tauri v2 桌面版**（离线完整包解压即用）；开发者也可以直接用源码或便携分卷包在浏览器中运行。当前稳定版本为 **v1.7.0**（截至 2026-11-04）。

### 核心特性

- **视频与图像修复**：基于单步扩散模型的超分辨率与细节增强，本地推理、数据不出机器。
- **三档模型**：3B / 7B / 7B-Sharp，支持 FP16 / FP8 及 MXFP8 / NVFP4 / INT8-convrot 量化变体（`.safetensors`，不兼容 GGUF）。
- **显存友好**：GPU Block Swap 在推理时动态换入换出 Transformer 块；3B FP8 可在 8 GB 显存起步运行。
- **批量与断点续跑**：单文件修复 + 文件夹批量扫描，批量任务在 `data/checkpoints/` 记录进度，中断后可续跑。
- **开箱即用的桌面版**：Tauri 原生窗口、系统托盘、Windows Toast 通知、文件拖拽、单实例与崩溃自动恢复、托盘增量更新。
- **多语言与实时监控**：界面支持简体中文、繁体中文、英文、日文、法文；GPU/显存/内存与任务进度经 SSE 实时推送。

### 界面展示

> 以下截图均在云端 Linux（无 GPU）真实启动 Web UI 后截取，浅色主题；无 GPU 时界面如实显示降级状态。

**首页仪表盘** —— 一句话定位、入口卡片与状态角标。本图在无 GPU 云环境启动，角标显示「未检测到可用 GPU · 未加载」；有 GPU 时此处显示设备型号与当前模型。

![首页仪表盘](https://gcore.jsdelivr.net/gh/ReSerendipity/SeedVR2-Lite@main/docs/assets/readme-seedvr2-lite-1-home-20261026.png)

**修复工作台** —— 左侧拖拽上传图片/视频，右侧参数面板选择模型、输出分辨率与 VAE。无 GPU 时顶部提示「需要 NVIDIA CUDA GPU 才能执行修复」，界面与参数仍可完整查看。

![修复工作台](https://gcore.jsdelivr.net/gh/ReSerendipity/SeedVR2-Lite@main/docs/assets/readme-seedvr2-lite-2-workbench-20261026.png)

**高级参数** —— 展开 DiT 配置（最大分辨率、色彩校正、批处理大小），并可勾选「强制按参数重载模型」与一键「重置」。

![高级参数](https://gcore.jsdelivr.net/gh/ReSerendipity/SeedVR2-Lite@main/docs/assets/readme-seedvr2-lite-3-params-20261026.png)

**历史记录** —— 表格视图管理已完成任务，支持状态筛选与结果回看。

![历史记录](https://gcore.jsdelivr.net/gh/ReSerendipity/SeedVR2-Lite@main/docs/assets/readme-seedvr2-lite-4-history-20261026.png)

**系统状态** —— GPU/显存/利用率与内存监控。本图为无 GPU 云环境：GPU 卡片标记 UNAVAILABLE、显存字段为 `--`；真实 GPU 环境此处显示显卡型号、显存占用与利用率曲线。

![系统状态](https://gcore.jsdelivr.net/gh/ReSerendipity/SeedVR2-Lite@main/docs/assets/readme-seedvr2-lite-5-system-status-20261026.png)

**整体架构** —— 浏览器/桌面壳 → FastAPI 后端 → 扩散推理引擎（DiT + Video VAE + Block Swap），本地存储模型、历史与输出。

![架构图](https://gcore.jsdelivr.net/gh/ReSerendipity/SeedVR2-Lite@main/docs/assets/readme-seedvr2-lite-7-architecture.svg)

### 快速开始

> 推荐普通用户直接下载[桌面版离线完整包](https://github.com/ReSerendipity/SeedVR2-Lite/releases)，解压即用。以下为从源码运行的路径。

**1. 环境**

- Windows x64、NVIDIA CUDA GPU（必须，不支持纯 CPU 推理）、Python 3.12+。
- 磁盘预留 ≥ 15 GB；视频修复需自行安装 FFmpeg 并加入 PATH（因许可证原因不随包分发，见 [NOTICE](NOTICE)）。

**2. 安装依赖**

```bash
git clone https://github.com/ReSerendipity/SeedVR2-Lite.git
cd SeedVR2-Lite

# Windows 一键安装（推荐）
install.bat

# 或开发者用 uv（自动创建 .venv 并安装 CUDA 版 PyTorch）
uv sync
# 驱动较旧时，改 pyproject.toml 中 [[tool.uv.index]] 的 url 为 cu121 / cu132 后重跑
```

**3. 下载模型**

```bash
python scripts/download_model.py --size 3b      # 3B + VAE + 文本嵌入，约 20 GB
# 7B：--size 7b ；7B-Sharp：--size 7b_sharp
```

大陆网络下载慢时先设置镜像：`set HF_ENDPOINT=https://hf-mirror.com` 再重跑下载脚本。权重必须放在 `model/` **根目录**（不要建子文件夹）。

**4. 启动**

```bash
start.bat
# 或：python app/integrated_app/app_server.py
```

浏览器打开 <http://127.0.0.1:7870>（端口被占用时自动顺延，以日志打印的地址为准）。

**5. 最小验证**

进入「修复工作台」上传一张图片 → 点击「上传并修复」→ 在「系统状态」页看到 GPU 占用上升、「历史记录」中出现一条已完成任务，即说明推理链路打通。

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
| Python | 3.12+（或使用桌面版内置便携 Python） |
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

### 显存与配置估算

> 站点 rewq.cn 实验区原有的交互式估算器（2026-11-08 下线）口径存档于此。官方值只取 [ByteDance-Seed/SeedVR](https://github.com/ByteDance-Seed/SeedVR) README 的 GPU Requirement 与 3B/7B 模型名（configs_3b / configs_7b）；**权重显存与相对负载是派生值，不含激活与框架开销**。官方没有给吞吐与单价，这里只算显存与配置，不编价格。

**在线交互版**：<https://reserendipity.github.io/SeedVR2-Lite/vram.html>（调 3B/7B、输出高度、帧数、GPU 数，实时读数；源文件 [docs/vram.html](docs/vram.html)，经 GitHub Pages 发布）。

**官方参考档位**：

| 场景 | 官方参考 |
|---|---|
| 100×720×1280（720p · 100 帧） | 1×H100-80G |
| 1080p / 2K（sp_size=4） | 4×H100-80G |

**派生口径**：

- 权重显存 ≈ 参数量 × 2 GB（bf16：3B ≈ 6.0 GB，7B ≈ 14.0 GB）
- 每卡相对负载 = (输出高度 ÷ 720)² × (帧数 ÷ 100)（以 720p · 100 帧为 1×）
- 所选 GPU 总显存 = 卡数 × 80 GB

**判定规则**：3B 卡数低于档位 → 「低于官方参考：建议 {n}×H100-80G」；7B 官方未单列档位，恒标注「以实测为准」；帧数超过 100 → 超出官方示例，需实测。

示例（原估算器给出的读数）：

| 模型 | 输出高度 | 帧数 | GPU 数 | 官方档位 | 权重显存（派生） | 每卡负载（派生） | 结论 |
|---|---|---|---|---|---|---|---|
| 3B | 720p | 100 | 1 | 1×H100-80G | ≈ 6.0 GB | 1.00× | 满足官方参考 |
| 3B | 1080p | 100 | 1 | 4×H100-80G | ≈ 6.0 GB | 2.25× | 低于官方参考：建议 4×H100-80G |
| 3B | 2K | 200 | 4 | 4×H100-80G | ≈ 6.0 GB | 8.00× | 满足官方参考 · 帧数超过官方示例的 100 帧，需实测 |
| 7B | 720p | 100 | 1 | 1×H100-80G | ≈ 14.0 GB | 1.00× | 7B 官方参考未单列，建议以实测为准 |
| 7B | 1080p | 100 | 4 | 4×H100-80G | ≈ 14.0 GB | 2.25× | 7B 官方参考未单列，建议以实测为准 |

> 工作台里的「VRAM 预检 & 推荐参数」是按本机实际显存的运行时预检，与这张静态参考表互补。

### 分辨率对比 · 480 / 1024

> 站点 rewq.cn 实验区「RESOLUTION · 480 / 1024」交互对比（2026-11-08 下线）的存档：同一张 AI 生成图（[魔搭 #259472](https://modelscope.cn/aigc/gallery/share/259472)，原图 SHA-256 前 8 位 `2b862db8`）的两个分辨率档，都是 WebP。

| 480 档（rewq.cn 省流档默认） | 1024 档 |
|---|---|
| ![480 档](https://gcore.jsdelivr.net/gh/ReSerendipity/SeedVR2-Lite@main/docs/assets/readme-res-cmp-259472-w480-20261108.webp) | ![1024 档](https://gcore.jsdelivr.net/gh/ReSerendipity/SeedVR2-Lite@main/docs/assets/readme-res-cmp-259472-w1024-20261108.webp) |
| 28.4 KB | 102.2 KB |

同一张图，字节差 **3.6×**——这是 rewq.cn 省流档把 w480 设为默认档的数据依据；两档的细节差异看发簪、竹叶和衣袖。

### 使用示例

```bash
# Docker 本地构建运行
docker build -t seedvr2 .
docker run --gpus all -p 7870:7870 seedvr2

# 或使用随 v* 标签发布的 GHCR 镜像
docker pull ghcr.io/reserendipity/seedvr2-lite:1.7.0
docker run --gpus all -p 7870:7870 ghcr.io/reserendipity/seedvr2-lite:1.7.0
```

环境变量模板见 `.env.example`，常用 `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`。

### 常见问题（FAQ）

- **模型文件未找到？** 权重必须放 `model/` 根目录，文件名与下载脚本输出一致，不要自行建子文件夹或改名。
- **显存不足（OOM）？** 改用 FP8 模型、开启 GPU BlockSwap、或降低输出分辨率；工作台的「VRAM 预检 & 推荐参数」会自动给出组合。
- **`install.bat` 装 PyTorch 失败？** 手动执行 `pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128`，按 `nvidia-smi` 换成对应 CUDA 版本。
- **视频修复没有输出？** 需自行安装 FFmpeg 并加入 PATH（图片修复开箱即用）。
- **HuggingFace 下载慢？** 设置 `HF_ENDPOINT=https://hf-mirror.com` 后重跑下载脚本。

### 安全与合规

> **网络绑定警告**：Web UI 默认仅绑定 `127.0.0.1`（`config.yaml` 的 `server.host`），默认未开启认证。**严禁改为 `0.0.0.0` 或公网 IP**，否则任何人都可调用推理 API、上传恶意文件或下载 outputs/uploads。如需局域网共享，请开启 Basic Auth 并在反向代理后启用 HTTPS。

使用前请阅读 [USER_AGREEMENT.md](docs/USER_AGREEMENT.md)。本项目是独立第三方社区工具，与字节跳动及其 Seed 团队无隶属、赞助或官方合作关系，也与 seedvr2.com 等付费商业站点无关；模型权重仅从官方来源（Hugging Face `ByteDance-Seed/SeedVR2-3B` / `SeedVR2-7B`）获取。组件许可与归属清单见 [NOTICE](NOTICE)。

### 文档与治理

- **文档**：仓内 [用户手册](docs/用户手册.md) 与 [开发者指南](docs/开发者指南.md)。
- **治理规范**：组织级治理与贡献约定见 [ReSerendipity/.github/governance](https://github.com/ReSerendipity/.github/tree/main/governance) 与 [贡献指南](https://github.com/ReSerendipity/.github/blob/main/CONTRIBUTING.md)。
- **架构决策（ADR）**：关键技术选型记录于 [`docs/adr/`](docs/adr/)。
- **发布治理**：版本发布、增量更新与回滚见 [release-governance](docs/release-governance.md)、[发布检查清单（历史记录，v1.5.1 时代）](docs/_archive/发布检查清单.md)、[增量更新发布手册](docs/增量更新发布手册.md) 与 [rollback_sop](docs/rollback_sop.md)；版本与变更见 [CHANGELOG.md](CHANGELOG.md)。

### 贡献与支持

参与贡献请遵循上述[组织级贡献指南](https://github.com/ReSerendipity/.github/blob/main/CONTRIBUTING.md)（Conventional Commits + DCO 签名）。Issue 与 Discussion 请在仓库内发起；安全问题请按仓库安全政策私下报告，勿公开提 issue。

- Releases（桌面版 / 便携包 / 增量更新）：<https://github.com/ReSerendipity/SeedVR2-Lite/releases>

### 许可证

本项目采用 [Apache License 2.0](LICENSE) 开源协议。Copyright 2024-2026 ReSerendipity。

---

## English

### Introduction

SeedVR2-Lite packages the [SeedVR2](https://huggingface.co/ByteDance-Seed) one-step diffusion restoration model (jointly open-sourced by ByteDance's Seed team and NTU S-Lab) into a **standalone local web app**: upload an image or video and get super-resolution / detail enhancement on the spot, with all data staying on your machine. It does **not** depend on ComfyUI or any external inference service.

The supported distribution is a **Tauri v2 desktop build** (offline bundle, extract-and-run); developers can also run it from source or via the portable bundle in a browser. Current stable release: **v1.7.0** (as of 2026-11-04).

### Highlights

- **Video & image restoration**: local super-resolution and detail enhancement via a one-step diffusion model; data never leaves the machine.
- **Three model tiers**: 3B / 7B / 7B-Sharp, with FP16 / FP8 and MXFP8 / NVFP4 / INT8-convrot quantized variants (`.safetensors`; GGUF is not supported).
- **VRAM friendly**: GPU Block Swap dynamically swaps Transformer blocks in/out during inference; 3B FP8 starts from 8 GB VRAM.
- **Batch & resumable**: single-file repair plus folder batch scanning, with progress persisted under `data/checkpoints/`.
- **Extract-and-run desktop**: native Tauri window, system tray, Windows toast notifications, drag & drop, single-instance + auto-restart, tray-based incremental updates.
- **i18n & live monitoring**: UI in Simplified/Traditional Chinese, English, Japanese and French; GPU/VRAM/memory and task progress streamed over SSE.

### Screenshots

> All screenshots were taken by actually launching the web UI on a headless cloud Linux box without a GPU (light theme); the degraded no-GPU state is shown honestly.

**Home dashboard** — tagline, entry cards and status pill. This shot was taken on a GPU-less cloud box, so the pill reads "No GPU detected · model not loaded"; on a GPU box it shows the device name and current model.

![Home dashboard](https://gcore.jsdelivr.net/gh/ReSerendipity/SeedVR2-Lite@main/docs/assets/readme-seedvr2-lite-1-home-20261026.png)

**Restore workbench** — drag & drop upload on the left; model / output resolution / VAE panel on the right. With no GPU the top shows "NVIDIA CUDA GPU required to run restoration", yet the UI and parameters remain fully inspectable.

![Restore workbench](https://gcore.jsdelivr.net/gh/ReSerendipity/SeedVR2-Lite@main/docs/assets/readme-seedvr2-lite-2-workbench-20261026.png)

**Advanced parameters** — DiT config (max resolution, color correction, batch size), plus a "force reload model with parameters" checkbox and a one-click reset.

![Advanced parameters](https://gcore.jsdelivr.net/gh/ReSerendipity/SeedVR2-Lite@main/docs/assets/readme-seedvr2-lite-3-params-20261026.png)

**History** — table view of completed tasks with status filters and result review.

![History](https://gcore.jsdelivr.net/gh/ReSerendipity/SeedVR2-Lite@main/docs/assets/readme-seedvr2-lite-4-history-20261026.png)

**System status** — GPU/VRAM/utilization and memory monitoring. This is the GPU-less cloud state: the GPU card is marked UNAVAILABLE and VRAM fields read `--`; on a real GPU box it shows the card model, VRAM usage and utilization curves.

![System status](https://gcore.jsdelivr.net/gh/ReSerendipity/SeedVR2-Lite@main/docs/assets/readme-seedvr2-lite-5-system-status-20261026.png)

**Architecture** — browser/desktop shell → FastAPI backend → diffusion engine (DiT + Video VAE + Block Swap), with local model/history/output storage.

![Architecture](https://gcore.jsdelivr.net/gh/ReSerendipity/SeedVR2-Lite@main/docs/assets/readme-seedvr2-lite-7-architecture.svg)

### Quick start

> Regular users should download the [desktop offline bundle](https://github.com/ReSerendipity/SeedVR2-Lite/releases) and extract it. Below is the from-source path.

**1. Requirements**

- Windows x64, an NVIDIA CUDA GPU (required; pure CPU inference is not supported), Python 3.12+.
- ≥ 15 GB disk; video restoration requires FFmpeg on PATH (not bundled for licensing reasons, see [NOTICE](NOTICE)).

**2. Install dependencies**

```bash
git clone https://github.com/ReSerendipity/SeedVR2-Lite.git
cd SeedVR2-Lite

# Windows one-click (recommended)
install.bat

# Or with uv (creates .venv and installs CUDA PyTorch)
uv sync
# For older drivers, change the [[tool.uv.index]] url in pyproject.toml to cu121 / cu132 and rerun
```

**3. Download weights**

```bash
python scripts/download_model.py --size 3b      # 3B + VAE + text embeddings, ~20 GB
# 7B: --size 7b ; 7B-Sharp: --size 7b_sharp
```

Slow downloads in mainland China: set `set HF_ENDPOINT=https://hf-mirror.com` first, then rerun. Weights must sit in the `model/` **root** (no subfolders).

**4. Launch**

```bash
start.bat
# or: python app/integrated_app/app_server.py
```

Open <http://127.0.0.1:7870> (auto-increments if busy; use the address printed in the log).

**5. Minimal verification**

In the workbench, upload an image → click "upload & restore" → on the "System status" page you should see GPU usage rise and a completed task appear in "History". That confirms the inference pipeline works.

### Requirements & compatibility

| Item | Requirement |
|---|---|
| OS | Windows (recommended; Linux via Docker from source) |
| GPU | NVIDIA CUDA GPU (required) |
| Python | 3.12+ (or the portable Python bundled in the desktop build) |
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

### VRAM & configuration sizing

> This archives the sizing logic of the interactive estimator that used to live in the rewq.cn lab section (retired 2026-11-08). Official values come only from the GPU Requirement section of the [ByteDance-Seed/SeedVR](https://github.com/ByteDance-Seed/SeedVR) README and the 3B/7B model names (configs_3b / configs_7b); **weight VRAM and relative load are derived values, excluding activations and framework overhead**. No throughput or pricing has been published officially, so this covers VRAM and configuration only — no invented prices.

**Interactive version**: <https://reserendipity.github.io/SeedVR2-Lite/vram.html> (tune 3B/7B, output height, frames and GPU count with live readouts; source at [docs/vram.html](docs/vram.html), served via GitHub Pages).

**Official reference tiers**:

| Scenario | Official reference |
|---|---|
| 100×720×1280 (720p · 100 frames) | 1×H100-80G |
| 1080p / 2K (sp_size=4) | 4×H100-80G |

**Derived figures** (same as the original estimator):

- Weight VRAM ≈ parameter count × 2 GB (bf16: 3B ≈ 6.0 GB, 7B ≈ 14.0 GB)
- Per-GPU relative load = (output height ÷ 720)² × (frames ÷ 100) (720p · 100 frames = 1×)
- Total VRAM of the selected GPUs = GPU count × 80 GB

**Verdict rules**: 3B with fewer GPUs than the tier → "below official reference: {n}×H100-80G recommended"; no official tier is published for 7B, so it is always marked "verify by benchmarking"; more than 100 frames → beyond the official example, benchmark required.

Examples (readings the original estimator would show):

| Model | Output height | Frames | GPUs | Official tier | Weight VRAM (derived) | Per-GPU load (derived) | Verdict |
|---|---|---|---|---|---|---|---|
| 3B | 720p | 100 | 1 | 1×H100-80G | ≈ 6.0 GB | 1.00× | Meets official reference |
| 3B | 1080p | 100 | 1 | 4×H100-80G | ≈ 6.0 GB | 2.25× | Below official reference: 4×H100-80G recommended |
| 3B | 2K | 200 | 4 | 4×H100-80G | ≈ 6.0 GB | 8.00× | Meets official reference · >100 frames exceeds the official example, benchmark required |
| 7B | 720p | 100 | 1 | 1×H100-80G | ≈ 14.0 GB | 1.00× | No official 7B tier; verify by benchmarking |
| 7B | 1080p | 100 | 4 | 4×H100-80G | ≈ 14.0 GB | 2.25× | No official 7B tier; verify by benchmarking |

> The workbench's "VRAM preflight & recommended parameters" is a runtime check against your actual local VRAM and complements this static reference table.

### Resolution comparison · 480 / 1024

> Archive of the "RESOLUTION · 480 / 1024" interactive comparison from the rewq.cn lab section (retired 2026-11-08): the same AI-generated image ([ModelScope #259472](https://modelscope.cn/aigc/gallery/share/259472), original SHA-256 first 8 hex `2b862db8`) in two resolution tiers, both WebP.

| 480 tier (rewq.cn data-saver default) | 1024 tier |
|---|---|
| ![480 tier](https://gcore.jsdelivr.net/gh/ReSerendipity/SeedVR2-Lite@main/docs/assets/readme-res-cmp-259472-w480-20261108.webp) | ![1024 tier](https://gcore.jsdelivr.net/gh/ReSerendipity/SeedVR2-Lite@main/docs/assets/readme-res-cmp-259472-w1024-20261108.webp) |
| 28.4 KB | 102.2 KB |

Same image, **3.6×** byte difference — the data basis for rewq.cn's data-saver mode defaulting to w480; the detail differences are in the hairpin, the bamboo leaves and the sleeves.

### Usage examples

```bash
# Build & run locally with Docker
docker build -t seedvr2 .
docker run --gpus all -p 7870:7870 seedvr2

# Or use the GHCR image published with v* tags
docker pull ghcr.io/reserendipity/seedvr2-lite:1.7.0
docker run --gpus all -p 7870:7870 ghcr.io/reserendipity/seedvr2-lite:1.7.0
```

Environment variables: see `.env.example`; a common one is `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`.

### FAQ

- **Model not found?** Weights must be in the `model/` root with the exact filenames printed by the download script; do not create subfolders or rename files.
- **Out-of-memory (OOM)?** Switch to an FP8 model, enable GPU BlockSwap, or lower the output resolution; the workbench's "VRAM preflight" recommends a combination automatically.
- **`install.bat` fails to install PyTorch?** Run `pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128`, adjusting the CUDA suffix to match `nvidia-smi`.
- **No output for videos?** Install FFmpeg and put it on PATH (image restoration works out of the box).
- **Slow HuggingFace download?** Set `HF_ENDPOINT=https://hf-mirror.com` and rerun the download script.

### Security & compliance

> **Network binding warning**: the web UI binds to `127.0.0.1` only (`server.host` in `config.yaml`) and ships without authentication by default. **Never change it to `0.0.0.0` or a public IP** — otherwise anyone can invoke the inference API, upload malicious files, or download outputs/uploads. For LAN sharing, enable Basic Auth and put the app behind an HTTPS reverse proxy.

Please read [USER_AGREEMENT.md](docs/USER_AGREEMENT.md) before use. This is an independent third-party community tool with no affiliation to ByteDance or its Seed team, and no relation to commercial sites such as seedvr2.com; weights are fetched only from official sources (Hugging Face `ByteDance-Seed/SeedVR2-3B` / `SeedVR2-7B`). Third-party licenses and attributions are listed in [NOTICE](NOTICE).

### Documentation & governance

- **Docs**: in-repo [user manual](docs/用户手册.md) and [developer guide](docs/开发者指南.md).
- **Governance**: org-level governance and contribution conventions live in [ReSerendipity/.github/governance](https://github.com/ReSerendipity/.github/tree/main/governance) and the [contributing guide](https://github.com/ReSerendipity/.github/blob/main/CONTRIBUTING.md).
- **Architecture decisions (ADR)**: key design records are under [`docs/adr/`](docs/adr/).
- **Release governance**: versioning, incremental updates and rollback are covered by [release-governance](docs/release-governance.md), the [release checklist (historical, v1.5.1 era)](docs/_archive/发布检查清单.md), the [incremental-update manual](docs/增量更新发布手册.md) and [rollback_sop](docs/rollback_sop.md); see [CHANGELOG.md](CHANGELOG.md) for versions and changes.

### Contributing & support

Follow the [org-level contributing guide](https://github.com/ReSerendipity/.github/blob/main/CONTRIBUTING.md) (Conventional Commits + DCO sign-off). Open issues and discussions in the repository; report security problems privately per the security policy rather than in a public issue.

- Releases (desktop / portable bundle / incremental updates): <https://github.com/ReSerendipity/SeedVR2-Lite/releases>

### License

Released under the [Apache License 2.0](LICENSE). Copyright 2024-2026 ReSerendipity.
