# SeedVR2-Turbo 许可证合规台账（License Compliance）

> 最后更新：2026-08-30（新增 §3.2 Comfy-Org 转包权重目录，ModelScope API 实测枚举）。
> ⚠️「商用合规 / 合规要求」两列仅记录**事实与风险提示**，不构成法律意见；标「需人工确认」者必须人工补查后再分发。

## 1. 主程序许可

| 组件 | 许可证 | 商用合规 | 合规要求 |
|---|---|---|---|
| SeedVR2-Turbo 项目代码 | Apache-2.0（以根级 LICENSE 为准） | 宽松许可 | 保留版权声明与 NOTICE |

## 2. 随仓内嵌模型实现（`model_lib/`，ByteDance 上游派生）

> 本仓 **无内嵌 ComfyUI 内核**（实测无 `comfy_kernel/`）；`model_lib/` 为逐目录 vendor 的模型实现，溯源见 `model_lib/SOURCE.md`。

| 组件 | 许可证 | 商用合规 | 合规要求 |
|---|---|---|---|
| `model_lib/dit/`（NaDiT） | Apache-2.0（Copyright 2025 ByteDance Ltd.） | 宽松许可（与主许可同源） | 保留原版权头；随包附 NOTICE |
| `model_lib/dit_v2/`（NaDiT v2，3B 现役主推理路径） | Apache-2.0（Copyright 2025 ByteDance Ltd.） | 宽松许可（与主许可同源） | 保留原版权头；随包附 NOTICE |
| `model_lib/video_vae_v3/`（SD3 因果 Video VAE） | Apache-2.0（Copyright 2023 HuggingFace Team / 2025 ByteDance Ltd.） | 宽松许可 | 保留原版权头；随包附 NOTICE |
| `model_lib/common/`（diffusion/distributed 等） | Apache-2.0（同上游） | 宽松许可 | 保留版权头 |

## 3. 模型权重

| 组件 | 许可证 | 商用合规 | 合规要求 |
|---|---|---|---|
| SeedVR2 权重（`model/`，`scripts/download_model.py` 下载） | 以 HuggingFace 仓库（numz/SeedVR2_comfyUI（Comfy-Org 转包版权重同源）与 ByteDance-Seed/SeedVR2-3B、ByteDance-Seed/SeedVR2-7B）与 NOTICE 为准 | 需人工确认 | 权重许可独立于代码许可 |

### 3.2 Comfy-Org/SeedVR2 转包与量化变体（2026-08-30 登记）

> 来源：[ModelScope Comfy-Org/SeedVR2](https://modelscope.cn/models/Comfy-Org/SeedVR2/tree/master)（HuggingFace 同名镜像仓）。
> 性质：Comfy Org 用 [comfy-model-tools](https://github.com/Comfy-Org/comfy-model-tools) 的 `seedvr2_convert.py` 对
> **ByteDance-Seed/SeedVR2-3B / SeedVR2-7B** 原始权重的单文件转包与量化，非独立模型。
> 许可：ModelScope 仓元数据标 **Apache-2.0**；上游 ByteDance 原始权重（HF 卡片与 GitHub 仓）同为 Apache-2.0，
> 量化变体（int8_convrot / mxfp8 / nvfp4）系衍生转换，许可随原权重。
> SHA256 / 体积为 ModelScope API 权威值（master 修订），登记用途：完整性锚点与分发核验。

| 文件 | 精度/类型 | 体积 | SHA256 |
|---|---|---|---|
| `diffusion_models/seedvr2_3b_fp16.safetensors` | 3B FP16 | 6.32 GB | `98669fd2c06df5eca88baf68cd5c478775c8e61fc110e598c52b350145ea2660` |
| `diffusion_models/seedvr2_3b_fp8_e4m3fn.safetensors` | 3B FP8 (E4M3FN) | 3.16 GB | `a0226eaa2c3e6f47ae5ce83225120f16479da890ced1a3bc32b1a14619787914` |
| `diffusion_models/seedvr2_3b_int8_convrot.safetensors` | 3B INT8 (convrot) | 3.22 GB | `c3dec8bcc5916843a8a858572970597462e1f2dc598d6dfd818f6cd40f53a157` |
| `diffusion_models/seedvr2_3b_mxfp8.safetensors` | 3B MXFP8 | 3.31 GB | `768623e3bfb1752b4d0668782751b5fead58b1bcb153f0b5e03a423095630297` |
| `diffusion_models/seedvr2_3b_nvfp4.safetensors` | 3B NVFP4 | 1.86 GB | `c8dea38b04d43295621726e2cd371c0d2d001006169c113aea17950f2cb2e295` |
| `diffusion_models/seedvr2_7b_fp16.safetensors` | 7B FP16 | 15.35 GB | `2742ca6fee63bc5cc1773f426dd4b07b78cad27f51c9ea5cd42b035e6b592252` |
| `diffusion_models/seedvr2_7b_fp8_e4m3fn.safetensors` | 7B FP8 (E4M3FN) | 7.68 GB | `5065e77d647dd553d9090a81e20d6de590d931a61df79d785e008433926ee418` |
| `diffusion_models/seedvr2_7b_int8_convrot.safetensors` | 7B INT8 (convrot) | 7.76 GB | `5aa0d25fc9d35e449b659d0c9a5dcb22e2a4fa04032101b95a39da42b32c1be6` |
| `diffusion_models/seedvr2_7b_mxfp8.safetensors` | 7B MXFP8 | 7.99 GB | `b40804f47910d96c5089c728cc7ec8b57b956750eabb6397dc4e6e697477263d` |
| `diffusion_models/seedvr2_7b_nvfp4.safetensors` | 7B NVFP4 | 4.43 GB | `cc4af1a7bd5377066496f393555478323e806fa21163bdbe3409451aface9b93` |
| `diffusion_models/seedvr2_7b_sharp_fp16.safetensors` | 7B-Sharp FP16 | 15.35 GB | `70823bca54b9c24eeb56e1c452697c7c2a430867e58db0e376c6e260f3a4489d` |
| `diffusion_models/seedvr2_7b_sharp_fp8_e4m3fn.safetensors` | 7B-Sharp FP8 (E4M3FN) | 7.68 GB | `7602c5f70868d28e7730035e4e9d745b05d661c8f0a7eb758e63f9c8603596ef` |
| `diffusion_models/seedvr2_7b_sharp_int8_convrot.safetensors` | 7B-Sharp INT8 (convrot) | 7.76 GB | `db48be2f1cc7e36b01a2aa529810f5d9c6a971edd29be225cf1b0eb18d51c366` |
| `diffusion_models/seedvr2_7b_sharp_mxfp8.safetensors` | 7B-Sharp MXFP8 | 7.99 GB | `0d621ec1561a11ca9b5f432ec6d4e09b263b61f4b83b0280552c8b4add030ec3` |
| `diffusion_models/seedvr2_7b_sharp_nvfp4.safetensors` | 7B-Sharp NVFP4 | 4.43 GB | `80d57af7722f5a5bd4c01d2ab2688f2bf05e552e59d3d3287257de709db10397` |
| `vae/ema_vae_fp16.safetensors` | VAE FP16 | 0.47 GB | `20678548f420d98d26f11442d3528f8b8c94e57ee046ef93dbb7633da8612ca1` |
| `vae/seedvr2_ema_vae_fp16.safetensors` | 同上（重复文件名） | 0.47 GB | `20678548f420d98d26f11442d3528f8b8c94e57ee046ef93dbb7633da8612ca1` |

> ⚠️ **与项目运行时权重的关系（实测 SHA256 对比）**：Comfy-Org 主权重（`seedvr2_3b_fp16` 等，无 `ema` 前缀）
> 与本仓 `config.yaml` 当前引用的 numz/SeedVR2_comfyUI 版（`seedvr2_ema_3b_fp16` 等）**字节不同、不可互换**；
> 仅 VAE 文件 `ema_vae_fp16.safetensors` 哈希与本项目一致（`20678548…`，同内容）。
> ✅ **2026-09-02 更新——已接入运行时并真机验证通过**：`config.yaml` 已按本表哈希登记
> `checkpoint_/sha256_{int8_convrot,mxfp8,nvfp4}`（fp16/fp8 保持 numz 源不动），下载路由
> （`download_model.py --precisions`）与加载期反量化（`app/integrated_app/engines/quant_dequant.py`）
> 已落地。**真机验证（2026-09-02，RTX 5070 Ti Laptop 12GB，3B 三精度）**：
> ① 三文件从 ModelScope 下载完成，SHA256 与本表权威值逐字一致；
> ② 反量化约定核对通过——int8_convrot 码一致率 100%（Hadamard 核/旋转方向/scale 语义正确），
> mxfp8 码一致率 93-95%（E4M3 舍入 ±1 LSB 正常量化噪声），nvfp4 排除 E2M1 ±0 符号抖动后码一致率 100%；
> ③ 真机加载推理冒烟通过——三精度各输出 1024×1200 合法图片（mean=76.5, std=68.7），
> 与同输入 FP16 结果结构一致。Comfy-Org 的 fp16/fp8 转换版
> （本表 `seedvr2_3b_fp16` 等 6 行）此前保持仅登记不接入。
>
> ✅ **2026-09-13 更新——fp16/fp8 双命名互认（GOTCHAS #122）**：上句所述 Comfy-Org fp16/fp8 转换版
> 与本仓 `config.yaml` 原引的 numz 版**字节不同**（fp8：numz `3bf1e43e…` vs 本表 `a0226eaa…`），
> 但模型结构一致（3B 实测参数键 635/635 命中，仅多 2 个无关 conditioning 键）。加载链已按
> 「命名等价」接受两者任一：`app/integrated_app/utils/weight_names.py` 统一解析别名
> （`seedvr2_ema_*` ↔ `seedvr2_*`），`config.yaml` 补 `sha256_{fp16,fp8}_alt` 登记本表 Comfy-Org 哈希，
> 命中主哈希或 `_alt` 哈希任一即通过白名单（完整性门禁**不因别名放松**）。
> 即：**同一精度的两套命名文件均可直接放入 `model/` 使用，无需改名或重新下载。**

## 4. 维护约定

- `model_lib/SOURCE.md` 为溯源基线：新增/升级 vendored 模型代码，**必须**同步更新它与本台账。
- 复核命令（只读）：`Get-ChildItem model_lib -Directory`；`Get-Content model_lib/SOURCE.md -TotalCount 5`。
