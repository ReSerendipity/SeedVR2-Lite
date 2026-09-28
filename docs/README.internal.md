# SeedVR2-lite 对内项目说明（DOCS-README.internal.md）

> 本文件为**对内**维护者说明，入库后位于 `DOCS/README.internal.md`，是 `DOCS/` 对内说明的唯一入口。
> 面向 GitHub 访客的对外文档只有根目录 `README.md`。凡「陌生用户读了无法行动」的内容——本地环境约定、部署细节、实验产物清单、内部字段引用——一律收在本文件，不得写回根 README。

## 1. 本地环境约定

- 项目根目录已就绪一份 `.venv`（系统 Python 3.12.10 @ `C:\Python312` 创建），依赖与 CUDA torch 均已装入其中。
- 走命令行时请先 `.venv\Scripts\activate`；或直接双击 `start.bat` / `install.bat`（脚本会自行检测并复用该环境）。
- 该说明属维护者本机环境事实，不进对外 README：普通用户按「快速上手」从零安装即可。

## 2. 文档站与在线演示的部署机制

- 文档站由 `website/` 目录的 VitePress 构建；在线演示来自 `demo/` 目录。
- 两者一并由 `.github/workflows/pages-deploy.yml` 部署到 GitHub Pages；演示目录说明见 `demo/README.md`。
- 对外 README「📖 文档 / 🧪 在线演示」只保留链接，CI 部署细节收本节。

## 3. 实验性量化文件（研发实验产物）

- `model/` 内另存的实验性量化文件：`seedvr2_3b_int8_convrot.safetensors`、`seedvr2_3b_nvfp4.safetensors`、`seedvr2_3b_mxfp8.safetensors`。
- 属研发实验产物，非 README 推荐档位；评测/生产使用 FP16/FP8 档位。
- 对外 README 仅保留一句「仓库 `model/` 内另存有少量实验性量化文件，属研发实验产物、非推荐档位」。

## 4. 显存表数据来源与 FP8 实现细节

- 显存表「最低显存」来自 `config.yaml` 的 `model.models.*.min_vram_*_gb`；对外改述为「数据来自项目内置配置」。
- FP8 实现细节（内部）：当前项目的 FP8 实现**仅用于权重存储格式**，推理时权重仍按 FP16/FP32 加载，尚未实现真正的 FP8 计算内核，因此 FP8 与 FP16 推理速度基本相同。
- 对外表述改为用户视角：「FP8 仅影响显存占用，不影响推理速度」，并保留 BlockSwap / 分辨率对速度的影响提示。

## 5. 环境变量内部注记

- `KMP_DUPLICATE_LIB_OK`（Intel OpenMP 兼容，一般不用改）——已自对外 README「环境变量配置（.env）」移出，收本节。
- `.env` 常用变量中对外保留 `PYTORCH_CUDA_ALLOC_CONF`（`expandable_segments:True` 减少显存碎片化）。

## 6. 版权声明渲染机制（内部）

- UI 设置页版权区块通过 `app/integrated_app/locales/*.json` 的 `settings.copyright_notice` 渲染。
- 对外「归属权与版权」仅列「UI 设置页版权区块」，渲染机制细节收本节；Apache 2.0 第 4 条义务清单（含不得移除 UI 设置页、启动日志中的归属展示）保留在对外。

## 7. 对外/对内拆分备忘

- 快速上手开头 `.venv` blockquote 整体删除，收本文件 §1。
- 「📖 文档 / 🧪 在线演示」删 `pages-deploy.yml` 部署说明与 `website/` 构建细节，收本文件 §2。
- 模型格式章实验产物长注精简为一句，文件名收本文件 §3。
- 显存表注记删 `config.yaml` 字段引用；FP8 说明改用户视角，收本文件 §4。
- `KMP_DUPLICATE_LIB_OK` 移内部，收本文件 §5。
- 归属权与版权删 locales 渲染机制细节，收本文件 §6。
- 其余章节（便携包、新手必看、快速导航、界面预览、技术特点、环境要求、模型共享模式、VRAM 预检、断点续跑、i18n、保姆级教程与模型下载、FAQ、备选运行方式、Docker、项目结构、技术栈、网络绑定安全警告、合规说明、独立第三方声明、许可证）原样保留。
