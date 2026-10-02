# SeedVR2-lite

![Version](https://img.shields.io/badge/version-1.6.1-blue?style=for-the-badge) ![License](https://img.shields.io/badge/license-Apache%202.0-green?style=for-the-badge) ![Python](https://img.shields.io/badge/python-3.12+-yellow?style=for-the-badge&logo=python&logoColor=white) ![GPU](https://img.shields.io/badge/GPU-NVIDIA%20CUDA-76B900?style=for-the-badge&logo=nvidia&logoColor=white) ![Models](https://img.shields.io/badge/model-3B%20%7C%207B%20%7C%207B--Sharp-ff69b4?style=for-the-badge) [![CI](https://github.com/ReSerendipity/SeedVR2-lite/actions/workflows/ci.yml/badge.svg)](https://github.com/ReSerendipity/SeedVR2-lite/actions) [![gitleaks](https://img.shields.io/badge/secret%20scan-gitleaks%20passing-0080FF?style=for-the-badge)](https://github.com/ReSerendipity/SeedVR2-lite/actions/workflows/gitleaks.yml)

**基于 SeedVR2 扩散模型的视频与图像超分辨率修复工具箱 — 独立运行、一键修复，无需 ComfyUI**

> SeedVR2-lite: a standalone video & image super-resolution toolkit powered by SeedVR2 diffusion models. One-click restoration via Web UI — no ComfyUI required.

## 它能做什么

- **视频与图像修复**：超分辨率、细节增强，本地推理、数据不出机器
- **零依赖开箱即用**：桌面安装版一键安装（含便携 Python、CUDA 版 torch 与 3B 模型），网页版/便携包解压即用
- **三档模型可选**：3B / 7B / 7B-Sharp，支持 FP16 / FP8 及 INT8-convrot / MXFP8 / NVFP4 量化变体
- **显存友好**：GPU Block Swap 动态换入换出 Transformer 块，FP8 + BlockSwap 可在更低显存上跑大模型
- **批量与断点**：单文件修复 / 文件夹批量扫描，批量任务支持断点续跑
- **多语言界面**：中文、繁体中文、英文、日文、法文

## 快速体验

| 想做什么 | 入口 |
|---|---|
| 在线体验界面（无需 GPU / 模型） | [模拟演示站](https://reserendipity.github.io/SeedVR2-lite/) |
| 从零开始安装运行（保姆级教程） | [完整文档站](https://reserendipity.github.io/SeedVR2-lite/docs/) |
| 下载桌面版 / 便携包 | [GitHub Releases](https://github.com/ReSerendipity/SeedVR2-lite/releases) |
| 模型格式 / 直链 / 显存对比 | [模型选型](#模型选型) |

## 桌面版（对外分发，推荐）

对外正式分发方式为桌面版：Tauri v2 原生窗口 + NSIS 安装器，一键安装、像普通软件一样使用与卸载。

- **一键安装**：`SeedVR2-Setup-vX.Y.Z.exe` + 3 个数据分卷（`SeedVR2-Data.7z.001/.002/.003`），双击即用——已含全部依赖与 3B MXFP8 模型，开机即用、无需联网
- **原生体验**：独立窗口、系统托盘（显示/隐藏/检查更新/退出）、Windows Toast 通知、文件拖拽、窗口状态记忆
- **增量更新**：托盘「检查更新」自动下载应用代码更新包（约 1.3MB）→ 校验 → 原子换载 → 重启，失败自动回滚
- **单实例与崩溃恢复**：重复启动自动聚焦已有窗口；Python 后端意外退出自动重启
- **卸载干净**：安装/卸载自动终止运行中的程序，卸载清理注册表与快捷方式

> 桌面版与网页版共享同一套 Python 后端与模型。当前稳定版 **v1.6.1**（[Releases](https://github.com/ReSerendipity/SeedVR2-lite/releases/latest)）。
>
> **取包提醒**：`/releases/latest` 只有增量更新包，首次安装不在那里。按用途取包：
>
> | 你要什么 | 去哪儿拿 |
> |---|---|
> | 首次安装桌面版（安装器 + 3 个数据分卷，约 5.3 GB） | [v1.5.4](https://github.com/ReSerendipity/SeedVR2-lite/releases/tag/v1.5.4)：`SeedVR2-Setup-v1.5.4.exe` + `SeedVR2-Data.7z.001/.002/.003` + `SHA256SUMS-v1.5.4.txt` |
> | 已装用户走托盘「检查更新」（约 1.4 MB） | [`/releases/latest`](https://github.com/ReSerendipity/SeedVR2-lite/releases/latest)：`app-v1.5.7.zip`（+ `.sha256`） |
> | 便携分卷包（`core` / `torch` / `model-*`，约 5.6 GB） | [v1.5.6](https://github.com/ReSerendipity/SeedVR2-lite/releases/tag/v1.5.6)：5 个分卷 + `manifest.json` + `SHA256SUMS.txt(.gpg)` + 两个解包脚本。代码等同 v1.5.7，**不含**其后合入主干的修复（见下节说明） |

## 网页版 / 便携包（开发与内部使用）

不想装 Python、不想配环境时，可用便携分卷包（已含便携 Python、全部依赖与 3B FP8 模型）或直接源码运行：

> **资产位置与基线**：便携分卷包公开挂在 [v1.5.6](https://github.com/ReSerendipity/SeedVR2-lite/releases/tag/v1.5.6)（该 tag 当初误发到增量通道，现作为便携包入口保留；`/releases/latest` 指向的 v1.5.7 只有 1.4 MB 增量包，不含分卷）。该包代码等同 **v1.5.7**，因此**不含**其后合入主干的修复——其中与安全相关的是 i18n 语言代码可越出 `locales/` 读取任意 `.json`（`POST /api/system/locale` 的 `locale` 字段此前无校验）与 checkpoint 任务 ID 的 sink 侧加固。应用只绑 `127.0.0.1` 且有 basic auth / CSRF / 限流，暴露面有限，但对外的正式分发仍建议用上一节的桌面版。

源码运行（不依赖分卷包）：`python -m venv .venv` → `.venv\Scripts\pip install -r requirements.txt`（torch 需装 CUDA 版，命令见[完整文档站](https://reserendipity.github.io/SeedVR2-lite/docs/)）→ `.venv\Scripts\python app/integrated_app/app_server.py`，浏览器打开 <http://127.0.0.1:7870>。

便携分卷包步骤：

1. 从 [Releases](https://github.com/ReSerendipity/SeedVR2-lite/releases) 下载 `core` / `torch` / `model-shared` / `model-fp8` 四个组件的**全部** `.00N` 分卷，外加 `manifest.json`、`SHA256SUMS.txt`、`unpack_portable_bundle.ps1`、`portable_bundle_lib.ps1`（合计约 5.6 GB）
2. 放进同一文件夹，执行 `powershell -ExecutionPolicy Bypass -File .\unpack_portable_bundle.ps1 -TargetDir D:\SeedVR2`
3. 双击 `SeedVR2-Portable\start-portable.bat`，浏览器打开 <http://127.0.0.1:7870>

要求：Windows x64 + NVIDIA 显卡（显存 ≥ 8 GB）+ 磁盘 ≥ 15 GB。图片修复开箱即用；视频修复需自行安装 FFmpeg 并加入 PATH（许可证原因不随包分发，见 [NOTICE](NOTICE)）。

## 界面预览

*浅色主题 — 首页仪表盘 / 修复工作台 / 历史记录 / 系统状态 / 模型设置 / 多语言切换*

![首页浅色](docs/screenshots/current/light/01-home-full.png)
![修复浅色](docs/screenshots/current/light/02-restore-single-default.png)
![历史记录浅色](docs/screenshots/current/light/06-history-table-view.png)
![系统状态浅色](docs/screenshots/current/light/08-system-status-full.png)
![设置浅色](docs/screenshots/current/light/09-settings-full.png)
![多语言切换浅色](docs/screenshots/current/light/11-locale-dropdown-open.png)

## 快速上手（约 5 分钟）

目标：一台 Windows 电脑，从空白到打开网页完成第一次修复，无需编程基础。

1. **安装 Python 3.12+**：<https://www.python.org/downloads/>，安装时勾选 "Add python.exe to PATH"
2. **获取代码**：`git clone https://github.com/ReSerendipity/SeedVR2-lite.git`（或页面 `Code → Download ZIP`）
3. **安装依赖**：Windows 双击 `install.bat`；或开发者用 `uv sync`（读取 `pyproject.toml`，自动创建 .venv 并安装 CUDA PyTorch；驱动较旧时改 `pyproject.toml` 中 `[[tool.uv.index]]` 的 url 为 `cu121` / `cu132` 后重跑）
4. **下载模型**：`python scripts/download_model.py --size 3b`（3B + VAE + 文本嵌入，约 20 GB；7B 用 `--size 7b`，7B-Sharp 用 `--size 7b_sharp`）
5. **启动**：双击 `start.bat`，浏览器自动打开 <http://127.0.0.1:7870>
6. **开始修复**：进入「修复工作台」上传图片/视频 → 开始修复；「系统状态」页实时查看 GPU 占用与进度

> 大陆网络下载慢：先执行 `set HF_ENDPOINT=https://hf-mirror.com` 再重跑下载脚本。

## 模型选型

**格式**：`.safetensors`（非 GGUF / PTH）。精度支持 **FP16**（全精度，画质最佳）、**FP8**（E4M3FN，省显存）及 **INT8-convrot / MXFP8 / NVFP4** 三种 Comfy-Org 量化变体（加载期反量化，2026-09-02 真机验证）；不兼容 GGUF / INT4（修复类扩散模型中会明显损伤画质）。

| 模型 | 精度 | 最低显存 | 约内存 | 效果 |
|---|---|---|---|---|
| SeedVR2-3B | FP16 | 16 GB | ~12 GB | 最佳 |
| SeedVR2-3B | FP8 | 8 GB | ~8 GB | 略降 |
| SeedVR2-7B | FP16 | 24 GB | ~20 GB | 最佳 |
| SeedVR2-7B | FP8 | 12 GB | ~12 GB | 略降 |
| SeedVR2-7B-Sharp | FP16 | 24 GB | ~20 GB | 最佳（细节增强） |
| SeedVR2-7B-Sharp | FP8 | 12 GB | ~12 GB | 略降 |

配套必需文件（所有模型共用）：`ema_vae_fp16.safetensors`（视频 VAE）、`pos_emb.pt` / `neg_emb.pt`（文本嵌入）。完整下载方式、直链与量化变体说明见 [文档站](https://reserendipity.github.io/SeedVR2-lite/docs/)。

**选型建议**：显存 ≤ 12 GB 选 3B FP8（最低 8 GB）或 7B FP8 + BlockSwap；16–24 GB 选 3B FP16 或 7B FP8；≥ 24 GB 选 7B-Sharp FP16。

> FP8 当前仅作权重存储格式，推理仍按 FP16/FP32 加载，故 FP8 与 FP16 推理速度基本相同；真正影响速度的是 BlockSwap（降低 20–70%）、分辨率（2048² 比 1024² 慢 3–4 倍）与帧数。

## 环境要求

| 项目 | 要求 |
|---|---|
| 操作系统 | Windows（推荐） |
| GPU | NVIDIA CUDA GPU（必须，不支持 CPU 推理） |
| Python | 系统 Python 3.12+（推荐），或自行下载 WinPython 3.12 解压到项目根目录 `WPy64-312101/`（可运行 `scripts\setup_winpython.py` 自动配置） |

### 常见问题

| 问题 | 解决 |
|---|---|
| 模型文件未找到 | 权重必须放 `model/` **根目录**（不要建子文件夹），文件名与下载脚本输出一致 |
| `install.bat` 装 PyTorch 失败 | 手动执行 `pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128`（按 `nvidia-smi` 换成对应 CUDA 版本） |
| 显存不足（OOM） | 改用 FP8 模型 / 开启 BlockSwap / 降低输出分辨率 |
| 端口被占用 | 应用自动寻找下一个可用端口，以日志打印地址为准 |
| HuggingFace 下载慢 | 设置 `HF_ENDPOINT=https://hf-mirror.com` 后重跑下载脚本 |

## 技术特点

| 特性 | 说明 |
|---|---|
| 单步扩散修复 | 基于扩散模型的单步推理，高效完成视频与图像超分辨率修复 |
| 独立运行 | 脱离 ComfyUI，FastAPI + Jinja2 提供完整 Web UI |
| 多模型配置 | 3B / 7B / 7B-Sharp × FP16 / FP8，含 Comfy-Org 量化变体 |
| DiT 架构 | MM-DiT（多模态 Diffusion Transformer）+ Window Attention + RoPE |
| Video VAE | 基于 SD3 架构，支持时间分块与内存优化 |
| GPU Block Swap | 推理时 GPU/CPU 动态换入换出 Transformer 块，大幅降低显存需求 |
| 批量处理 | 单文件上传修复 + 文件夹批量扫描，支持断点续跑（`data/checkpoints/`） |
| VRAM 预检 | 按输入分辨率/模型/显存自动推荐参数组合（FP16 → FP8 → FP8+BlockSwap） |
| 多语言界面 | 中文、繁体中文、英文、日文、法文（`app/integrated_app/locales/`，三层回退） |
| 实时监控 | GPU 状态、系统内存、任务进度 SSE 实时推送 |

### 其他

- **模型共享**：`config.yaml` 的 `model.model_source_mode` 支持 `portable`（项目内 `model/`，默认）与 `shared`（外部共享目录）两种模式
- **环境变量**：根目录 `.env`（模板见 `.env.example`），常用 `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`
- **Docker（本地构建）**：`docker build -t seedvr2 . && docker run --gpus all -p 7870:7870 seedvr2`
- **Docker（GHCR 镜像）**：`docker pull ghcr.io/reserendipity/seedvr2-lite:<版本>`（镜像随 `v*` 标签发布，如 `v1.5.7` 对应 tag `1.5.7`），再 `docker run --gpus all -p 7870:7870 ghcr.io/reserendipity/seedvr2-lite:<版本>`

## 项目结构（概览）

```
SeedVR2/
├── app/integrated_app/   # 核心应用（FastAPI、引擎、路由、模板、static、locales、middleware）
├── common/               # 通用工具库（扩散调度、分布式、种子）
├── model_lib/            # 模型定义（dit / dit_v2 / video_vae_v3）
├── configs_3b/  configs_7b/   # 3B / 7B 模型配置
├── model/                # 预训练模型存放目录
├── docs/  website/  demo/     # 文档 / VitePress 文档站源码 / 在线模拟演示
├── scripts/  launcher/  perf/ # 辅助脚本 / 便携启动器 / 性能基准
├── tests/                # 测试套件（pytest + Playwright）
└── config.yaml  pyproject.toml
```

## 安全与合规

**网络绑定警告**：Web UI 默认仅绑定 `127.0.0.1`（`config.yaml` 的 `server.host`）。**严禁改为 `0.0.0.0` 或公网 IP**——本应用不含用户认证与权限隔离，直接暴露将导致任意调用推理 API 占用 GPU、上传恶意文件、下载 outputs/ 与 uploads/ 内容。如需局域网共享，请在反向代理后增加 Basic Auth 并启用 HTTPS。

**合规说明**：使用前请阅读 [USER_AGREEMENT.md](USER_AGREEMENT.md)。模型权重（SeedVR/SeedVR2）为 Apache-2.0；FFmpeg 为本地开发依赖，不随仓库分发（见 [NOTICE](NOTICE)）。对外分发物随附组件的许可与归属清单见 [NOTICE](NOTICE)；输出**画面内无可见标识、文件名沿用输入名**，内容标识走文件元数据与不可感知水印两层（实现与边界见 `docs/AI应用分发安全加固指南.md`）。

**第三方声明**：本项目是独立的第三方社区工具，基于字节跳动 Seed 团队与南洋理工大学 S-Lab 联合开源的 SeedVR2 模型（Apache-2.0）构建，与字节跳动及其 Seed 团队无隶属、赞助或官方合作关系；与 seedvr2.com / seedvr2.net 等付费商业站点无任何关系；模型权重仅从官方来源（Hugging Face `ByteDance-Seed/SeedVR2-3B` / `SeedVR2-7B`）获取。

## 技术栈

| 层级 | 技术 |
|---|---|
| 推理框架 | PyTorch (CUDA)、自定义 DiT、Video VAE (SD3) |
| 后端 | Python 3.12、FastAPI、Uvicorn |
| 前端 | Jinja2 模板、HTMX、原生 CSS/JS |
| 数据 | SQLite（历史记录）、SSE 实时推送 |
| 工具链 | Ruff、Black、Mypy、Pytest、Playwright |

## 贡献

参与贡献请遵循 [组织级贡献指南](https://github.com/ReSerendipity/.github/blob/main/CONTRIBUTING.md)（Conventional Commits + DCO 签名）。

## 许可证

本项目采用 [Apache License 2.0](LICENSE) 开源协议。版权所有 Copyright 2024-2026 ReSerendipity。
