# SeedVR2-Turbo 便携启动器（Portable Launcher）— SCAFFOLD

> 参考 **Mie-Package-Launcher** 模式：把 Python 环境 + 权重 + 模型依赖打包进一个
> 可移动目录；启动器通过**相对自身路径**自动定位一切并引导（bootstrap）环境，
> 使应用能从 U 盘 / 任意目录运行，**无需系统 Python**。

本目录是脚手架（scaffold）：结构完整、可直接运行形态，但**未固化**确切权重路径与
下载 URL（见文末「未决项」）。它复用了仓库现有的 `app/clean_launch.py` 入口、
`requirements.txt` / `requirements-lock.txt` 依赖清单、以及 `config.yaml` 的
`model.pretrained_dir=model` 约定。

## 目录结构

```
portable_launcher/            # 本目录；在真正打包时可整体作为 <pkg>/ 根
├── launcher.ps1              # Windows 引导脚本
├── launcher.sh               # Linux / macOS 引导脚本
├── README.md                 # 本文件
└── requirements.txt          # 启动器自身依赖清单占位（实际安装用的是包根的 requirements）
```

打包后的移动包形态（PKG_ROOT 即包根）：

```
<pkg>/
├── launcher.ps1 / launcher.sh
├── app/clean_launch.py       # 应用入口（PKG_ROOT 锚点）
├── model/                    # 权重目录（portable 模式）
├── venv/                     # 首次运行自动创建的本地 venv
├── python/                   # 可选：内置便携 Python（最优先，完全隔离）
├── wheels/                   # 可选：离线 wheel 缓存（优先离线安装）
├── requirements.txt          # 应用依赖（或 requirements-lock.txt）
└── config.yaml
```

> 在仓库内直接试运行本启动器时，它会自动上溯一级把仓库根当作 `PKG_ROOT`
> （因为 `app/clean_launch.py` 在仓库根）。

## 使用方法

### Windows
```powershell
powershell -ExecutionPolicy Bypass -File launcher.ps1
# 可选：指定模型尺寸提示 / 跳过依赖安装
powershell -ExecutionPolicy Bypass -File launcher.ps1 -Size 7b -SkipDeps
```
启动后浏览器打开 <http://127.0.0.1:7870>。

### Linux / macOS
```bash
chmod +x launcher.sh
./launcher.sh
# 可选
./launcher.sh --size 7b --skip-deps
```

## 启动器做了什么
1. **定位包根**：以脚本自身位置为基准解析 `PKG_ROOT`（自动探测 `app/clean_launch.py` 锚点）。
2. **定位 Python**：优先级 `python/`（内置便携）> `venv/` > 系统 `python`。
3. **创建 venv**：若无 `venv/`，从系统 Python 以 `python -m venv --copies` 创建，
   之后**直接调用** `<venv>/Scripts|bin/python` 执行（不依赖激活脚本，利于移动）。
4. **安装依赖**：存在 `wheels/` 则离线 `pip install --no-index --find-links wheels/`；
   否则在线安装 `requirements.txt`（缺失时回退 `requirements-lock.txt`）。
5. **设置环境变量**（全部相对、可移植）：`KMP_DUPLICATE_LIB_OK`、`PYTHONPATH=PKG_ROOT`、
   `PYTORCH_CUDA_ALLOC_CONF`、`TORCHINDUCTOR_CACHE_DIR`、`PYTHONUTF8`。
6. **权重检查**（非致命）：提示 `model/` 是否缺失 `ema_vae_fp16.safetensors` /
   `pos_emb.pt` / `neg_emb.pt`。
7. **启动**：`python app/clean_launch.py`。

## 与现有便携分卷包的关系
仓库已有 `scripts/build_portable_bundle.ps1` + `unpack_portable_bundle.ps1` +
`portable_bundle_lib.ps1` 的「分卷压缩离线包」体系（见 `launcher/release-notes-portable.md`）。
本启动器是**更轻量的互补方案**：不预打包、不拆分卷，而是用一份引导脚本在任意位置
现场 bootstrap 出可运行环境，适合开发调试与小规模分发。

## 未决项（需人工确认后落地）
- **确切权重路径**：以 `config.yaml` 的 `model.pretrained_dir: model` 为准，
  文件名须与文档一致（`seedvr2_ema_3b_fp16.safetensors` 等）。
- **模型下载 URL**：默认仓库 `numz/SeedVR2_comfyUI`；国内镜像
  `HF_ENDPOINT=https://hf-mirror.com`。3B 最小集：`python scripts/download_model.py --size 3b`。
- **venv 可移植性**：Windows venv 含绝对路径（`pyvenv.cfg` 的 `home`），换盘符可能失效；
  生产「纯净可移动」推荐内置 `python/`（WinPython 路线），本启动器已优先支持。
- **CUDA 平台**：应用主目标为 NVIDIA/CUDA + Windows；Linux/macOS 下按 `requirements`
  安装对应 CUDA torch 构建。
