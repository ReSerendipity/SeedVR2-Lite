# MLOps 质量门禁说明（ML Ops Gates）

> 本文档为**随仓库分发**的公开子集，固化「为什么覆盖率只统计 Web 层、引擎核心靠什么兜底」的决策理由。
> 维护者本地的 KNOWN_ISSUES / AGENTS.md / docs/reports 不随仓库分发，本文是其中与 ML 质量门禁相关、必须随代码走的最小集。

## 1. 覆盖率边界：为什么 engines/ 与 model_lib/ 不在单测网内

证据：`pyproject.toml` 的 `[tool.coverage.run]` 段。

- 覆盖率**只统计 `app/integrated_app` 的 Web 应用层**，当前实测 line-rate ≈ 65%，门禁 `fail_under = 55`（与 `.github/workflows/ci.yml` 对齐）。
- 被 omit 的三类路径及理由（逐类见 pyproject 注释）：
  1. **GPU-only 优化路径**（`optimization/gpu/*`）：张量操作必须真实 CUDA 才能跑，CPU 测试环境无法执行；
  2. **推理主路径**（`engines/_dit_pipeline.py`、`_vae_pipeline.py`、`seedvr2_engine.py`、`optimization/inference/*`）：需要已加载模型权重 + CUDA 张量；
  3. **vendored 上游研究代码**（`model_lib/`）：镜像 ByteDance SeedVR2 上游，按上游节奏同步，不做本地 strict 类型标注与单测（`pyproject.toml` mypy exclude 同口径）。
- 已在 CPU 上可测的纯计算面（diffusion sampling / post-processing / tile blend）**已移出 omit**，由 `tests/test_diffusion_sampling.py`、`test_post_processing.py`、`test_tile_blend.py` 覆盖—— omit 清单是「确需 GPU 才跑」的精确集合，不是逃避。

## 2. 引擎回归靠什么：真机双层质量门

证据：`.github/workflows/gpu-smoke.yml`。

 hosted runner 无 GPU，发布门禁只能证明「打包正确」。真机推理验收由 self-hosted GPU runner 承担，**每次触发跑两遍**：

| 层 | 内容 | 证明什么 |
|---|---|---|
| 发布包层 | 解包最新 Release，强制一次真实 `--require-inference --quality-gate` | 用户拿到的包真能修图 |
| 源码层（source-overlay） | 解包目录的 `app/` + `config.yaml` 换成当前分支源码，复用同一权重再跑一遍 | main 相对上一版在引擎/优化层有无回归 |

两层都带 `--min-psnr 15 --min-ssim 0.5` 硬阈值（`image_metrics.py` 计算），不达标即红。
触发：周一/周四定时 + Portable Release 成功后联动 + 手动 dispatch。runner 离线时自动建 skip-record issue，恢复后自动关闭，不留静默盲区。

## 3. 量化质量基线（跨精度 PSNR/SSIM 对比）

证据：`perf/benchmark/quant_quality_baseline.py` + `.github/workflows/gpu-smoke.yml` 的 `quant_baseline` 开关。

- 便携包内置 3B MXFP8；量化基线补下 FP8 作参考精度，同一确定性 golden 源图（固定 seed=42）跑两精度，产出对比 JSON 并以 artifact 归档。
- 开关默认关闭（需补下 ~8GB 权重 + 常驻服务真机对比，耗时显著）；手动 dispatch `quant_baseline: true` 触发。CI 只跑纯逻辑自测，真 GPU 产出需人工在真机跑后归档——这是有意的成本取舍，非缺失。

## 4. 运行时输出侧轻量质量信号（MLOps P-2，2026-10-06 接入）

证据：`app/integrated_app/metrics.py` 的 `compute_output_mean_luma()`、`tests/test_metrics.py` 的 `TestOutputMeanLuma`。

- 每次成功推理（图片产出）best-effort 采集输出图平均亮度（0-255，缩略到 64px 再算），近 100 次样本均值经 `/api/system/metrics` 暴露。
- 用途：最低成本探测「输出全黑」类静默劣化（模型错载/采样异常的典型症状）。失败/视频/解码异常一律记 None，不影响主流程。
- **当前边界**：这只是运行时观测字段，未接告警阈值；真正的质量回归仍以 §2 真机门为准。

## 5. 禁区与升级口径

- 改 §1 omit 清单、§2 质量门阈值（`--min-psnr/--min-ssim`）、§3 量化基线脚本，属 MLOps 门禁变更，须在 PR 中说明理由并同步本文。
- 与代码现实对账关系见 `docs/CODING_STANDARDS.md` §5（禁区与门禁口径）。
