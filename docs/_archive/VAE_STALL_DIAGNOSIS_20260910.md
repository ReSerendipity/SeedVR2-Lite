# SeedVR2-lite：VAE 阶段卡死 + 显存占用远高于 ComfyUI —— 诊断报告

- 日期：2026-09-10
- 环境：NVIDIA GeForce RTX 5070 Ti **Laptop** GPU / **11.94 GB** 显存 / 系统内存 31.7 GB
- 软件：PyTorch 2.13.0+cu132，cuDNN 92000
- 复现输入：1672×941 图片，`double_res=True` → 输出 **3344×1882**（7.9 MP），latent 234×418
- 卡死位置：`_image_pipeline.py:792 阶段3: VAE 解码`，最后一行日志是
  `VAE tiled 解码: tile_size=1024, overlap=128, gaussian_blend=True, groupnorm_accum=True`

> **✅ 状态（2026-09-10 18:00）：8 项修复全部落地，已通过 ast 解析 / import 冒烟 / 纯逻辑单测。**
> 改动：`_image_pipeline.py` · `_memory_utils.py` · `vae_tiled_enhance.py` · `_vae_pipeline.py` ·
> `gpu_utils.py` · `model_manager.py` · `bad_case_retry.py` · `routes/restore/common.py`。
> 协议义务：`docs/agents/GOTCHAS.md` 追加 #105–#110；AGENTS.md 升 v1.76；`REVISION_LOG.md` 追加 v1.76 行。
> （上述三个账本文件与维护者治理协议 `AGENTS.md` 同属本地治理层，未随仓库分发；对外可执行的
> 禁区与门禁口径见 `docs/CODING_STANDARDS.md` 第 5 节。）
> **待真机回归**：在 12 GB 卡上复跑「同分辨率出图 + double_res」确认不再卡死（当前环境无 GPU，仅静态/逻辑验证）。

---

## 结论速览

| 问题 | 根因 | 证据 |
|---|---|---|
| **显存比 ComfyUI 多约 4.3 GB** | nvfp4 权重在**加载期被全量反量化成 bf16**，驻留从 ~2 GB 变成 **6.31 GB** | 日志 `nvfp4 权重加载期反量化 (dtype=torch.bfloat16)` → `DiT 参数数量: 3,391,475,776, dtype=torch.bfloat16`；BlockSwap 报告 `Transformer blocks: 6353.96MB on cuda` |
| **VAE 阶段卡死** | 显存被吃满后 **Windows WDDM 驱动把显存页换到系统内存**，每个 CUDA kernel 慢 250~700 倍，表现为"卡死"而非报错 | 实测：同一个 64×64 latent 解码，空显存 **0.21 s** → 占用 6.79 GB 后 **54~148 s**；`mem_get_info()` 报告 **free = 0.00 GB** |
| **tile size 选得太大** | 推荐算法只看**总显存**，不看**空闲显存**；tile=1024 峰值 **5.45 GB**、tile=512 峰值 **1.79 GB**，而**总耗时完全相同** | `vae_tiled_enhance.py:71 / :109 / :127`；实测 8.34 s vs 8.34 s |

---

## 一、为什么"同一个模型，显存却比 ComfyUI 多得多"

### 1.1 量化权重在加载期被"反量化"掉了（主因，约 +4.3 GB）

日志链路：

```
[WARNING] fp16/fp8 均不存在，回退到可用精度 nvfp4
[INFO]    nvfp4 权重加载期反量化 (dtype=torch.bfloat16)...
[INFO]    Comfy-Org 量化权重反量化完成: 210 个 Linear (dtype=torch.bfloat16)
[INFO]    已将 425 个参数转换为 torch.bfloat16
[INFO]    DiT 参数数量: 3,391,475,776, dtype=torch.bfloat16
[INFO]    Transformer blocks: 6353.96MB on cuda, 0.00MB on cpu
```

代码位置：

- `app/integrated_app/engines/seedvr2_engine.py:725-737`
  → 调用 `quant_dequant.dequantize_state_dict(state_dict, dtype=dit_dtype)`
- `app/integrated_app/engines/quant_dequant.py:209-265`
  → 对每个 `*.comfy_quant` 条目就地把 `weight` 换成反量化后的 bf16 张量

结果：1.86 GB 的 nvfp4 文件在内存/显存里变成 **3.39 B × 2 B ≈ 6.79 GB** 的 bf16 权重。
ComfyUI 侧的量化模型是**以保持量化形态驻留、matmul 时按需反量化**，所以同一份权重大约只占 2 GB 左右。

**项目自己是知道这件事的** —— `app/integrated_app/gpu_utils.py:203-205`：

```python
- `mxfp8` / `int8_convrot` / `nvfp4`：加载期反量化，驻留 ≈ fp16 → 返回 `"fp16"`
```

也就是说：**在本项目里选 nvfp4 只省磁盘，不省显存**。

### 1.2 显存门槛在回退时被绕过

- `app/integrated_app/config_models.py:147` → `min_vram_nvfp4_gb: int = 16`（nvfp4 需要 16 GB 卡）
- 但 `app/integrated_app/model_manager.py:397-409` 的回退逻辑**只检查文件是否存在**，不校验显存门槛：

```python
all_precisions = ["fp16", "fp8", "mxfp8", "int8_convrot", "nvfp4"]
...
if model_cfg.get(f"checkpoint_{p}") and self.check_model_exists(model_size, p):
    found = p; break
...
logger.warning(f"{precision}/{fallback_precision} 均不存在，回退到可用精度 {found}")
```

于是 12 GB 的笔记本 GPU 被自动塞进了一个"需要 16 GB"的配置。日志里的
`fp16/fp8 均不存在，回退到可用精度 nvfp4` 就是这条路径。

### 1.3 另一处自相矛盾：OOM 自动降级链条假设 nvfp4 省显存

`app/integrated_app/bad_case_retry.py:312-321`：

```python
# 3. 第三次及以后：精度降级（按显存/质量从高到低：fp16 → fp8 → mxfp8 → int8_convrot → nvfp4）
fallback_chain = ["fp16", "fp8", "mxfp8", "int8_convrot", "nvfp4"]
```

按 1.1 的结论，`fp16 → nvfp4` 这一步**一点显存都不会省**，OOM 重试必然再次 OOM。

### 1.4 BlockSwap 没生效 + DiT 解码时不释放

- 本次请求 `blocks_to_swap=0`（`config.yaml:33` 默认 32，但请求传了 0）→ 32 个 block 全在 GPU（6353 MB）。
- `dit_cache_model` 默认 `True`（`_memory_utils.py:705`、`routes/restore/common.py:194`）
  → `_image_pipeline.py:782` 的 `self._destroy_dit()` **被跳过**，VAE 解码时 DiT 仍常驻。
  日志里阶段 2 之后**没有** `DiT销毁后`，直接跳到 `阶段3: VAE 解码`，可以佐证。

### 1.5 `double_res` 把输出放大到 7.9 MP

`routes/restore/common.py:364-380`：短边 941 × 2 = 1882 → 输出 3344×1882。
显存占用与像素数成正比，这一步直接把 VAE 解码的激活峰值推到 5 GB 量级。

---

## 二、为什么卡在 VAE（而不是报错）

### 2.1 tile size 用"总显存"而不是"空闲显存"来推荐

`app/integrated_app/optimization/inference/vae_tiled_enhance.py:71 / :109`：

```python
total_memory_mb = torch.cuda.get_device_properties(device).total_memory // (2**20)
...
elif total_memory_mb > 12 * 1000:  # 11.94GB -> 12226MB，刚好命中
    return 1024
```

11.94 GB → 12226 MB > 12000 → 推荐 **1024**（潜空间 128）。
而阶段 3 开始时显存已经 `6.85GB使用/7.99GB保留`，**空闲只剩约 4 GB**。

### 2.2 实测：tile=1024 白白多吃 3.7 GB，速度一点没快

`experiments/_diag_vae_bench.py`（空显存，latent 1×16×1×234×418）：

| tile/overlap | tile 数 | 总耗时 | 峰值显存 |
|---|---|---|---|
| 1024 / 128 | 8 | **8.34 s** | **5.45 GB** |
| 512 / 64 | 40 | **8.34 s** | **1.79 GB** |

两者**总耗时完全相同**，但 1024 的峰值显存是 512 的 3 倍。这一步是纯亏。

### 2.3 显存超载 → WDDM 换页 → 表现为"卡死"

`experiments/_diag_ab.py`（同一个 64×64 latent 的非 tiled 解码，预热后连测 3 次）：

```
DIT_GB=0.00   alloc=0.48GB  free=10.29GB   run0: 0.212s  run1: 0.233s  run2: 2.781s
DIT_GB=6.31   alloc=6.79GB  free= 0.00GB   run0: 54.357s run1: 1.027s  run2: 0.239s
```

注意两次测量里 **free 分别是 10.29 GB 和 0.00 GB** —— 说明这台机器上除本项目外，
**其它进程（桌面合成器 / 浏览器硬件加速等）已经占掉 1~5 GB**，可用额度是波动的。

另外单独测到：预占 6.31 GB 后，冷启动的第一次 64×64 解码耗时 **72 s（workaround 开）/ 148 s（workaround 关）**，
而空显存时是 **0.27 s** —— 慢 250~550 倍。

线上阶段 3 需要跑 8 个大 tile，每个峰值 5.45 GB，叠加后就是"几分钟没有任何输出"，看上去就是卡死。

### 2.4 卡死栈（faulthandler 抓取）

`experiments/_diag_stack.py`（诊断当日临时脚本，已随实验清理、现不在仓库），DiT 常驻 6.79 GB 时抓取：

```
File "model_lib\video_vae_v3\modules\causal_inflation_lib.py", line 204 in _conv_forward
File "torch\nn\modules\conv.py", line 735 in forward
File "model_lib\video_vae_v3\modules\causal_inflation_lib.py", line 376 in basic_forward
File "model_lib\video_vae_v3\modules\attn_video_vae.py", line 996 in forward
```

第 204 行是那个 **"Conv3d workaround（fixing VAE 3x memory bug）"** 里的 `torch.cudnn_convolution(...)`。

⚠️ **但这个 workaround 不是主因**：对照实验表明关掉它（`NVIDIA_CONV3D_MEMORY_BUG_WORKAROUND=False`）
一样是 72~148 s。它只是显存紧张时**最先被拖垮的一层**（`torch.cudnn_convolution` 走 cuDNN legacy 路径，
需要额外 workspace，在 WDDM 换页时开销被放大）。真正的原因是显存超载。

---

## 三、另外顺带发现的无效代码（不影响本次卡死，但建议清理）

- `GroupNormAccumulator`（`vae_tiled_enhance.py:426`）：`start_accumulation()` 只置了标志，
  `accumulate_from_tile()` **从来没有被 VAE 内部调用过** → `_var_lists` 始终为空，
  `apply_accumulated_stats()` 是空操作。日志里的 `groupnorm_accum=True` 实际什么也没做。
- `TiledVAEHook`（`vae_tiled_enhance.py:1224`）：patch 后的 decode 去找 `vae._internal_tile_state`，
  但 VAE 里没有这个属性 → `_last_tile_outputs` 恒为 `None` → 后面的 Gaussian 混合分支永远不执行。
  日志里的 `gaussian_blend=True` 同样是空转。

---

## 四、修复建议（按性价比排序）

### P0 —— 立刻止血

1. **阶段 3 之前释放 DiT** ✅ **已实施**
   `app/integrated_app/engines/_image_pipeline.py:782`
   改为：空闲显存 < `DIT_KEEP_RESIDENT_FREE_GB`(10GB) 时，经 `manage_model_device` 把 DiT **卸载到 CPU**
   （保留实例供下一任务复用，而非销毁），解码完由下一任务复用分支 `restore_model_to_gpu` 恢复：
   ```python
   else:
       free_gb = get_free_vram_gb(self.device)
       if free_gb < DIT_KEEP_RESIDENT_FREE_GB:
           offload_model_to_cpu(self.dit, "DiT", ...)
   ```
   单这一条就能腾出约 6.3 GB。视频流水线因 DiT/VAE 逐段交错，未改（逐段卸载净收益为负）。

2. **tile size 改成按"空闲显存"推荐** ✅ **已实施**
   `vae_tiled_enhance.py` 的 `get_recommend_*_tile_size` 改为用 `torch.cuda.mem_get_info()` 的
   free 值 + 实测峰值模型（`peak_gb ≈ 0.51 + 4.88e-6·tile_px²`，见 `_recommend_tile_from_free_vram`）。
   12 GB 卡 + DiT 常驻（free≈5GB）时落到 **768**（实测 1024 峰值 5.45GB ≈ 768 的 3 倍，且耗时相同）。

3. **给 tiled 解码加进度日志 + 卡死看门狗** ✅ **已实施**
   `engines/_vae_pipeline.py` 用实例级钩子 `_TileProgressLogger`（不碰 `model_lib/` 禁区）给每个 tile
   打 `用时 / 空闲显存`；`_VaeStallWatchdog` 在 >20s 仍无进展时把主线程栈 + 显存状态打到日志并提示 WDDM 分页，
   让"静默卡死"不再静默（不打断 CUDA 内核，安全）。

### P1 —— 消除"显存比 ComfyUI 多"的根因

4. **让量化格式回退不再误拒**（原 503 根因）：回退分支原本用 `min_vram_{p}_gb` 硬挡，
   12 GB 卡直接把 nvfp4 拒绝（nvfp4 需要 16 GB）→ 用户只下了 nvfp4 权重却 503「模型文件不存在」，
   且错误把"存在但被显存门槛排除"和"文件确实缺失"混为一谈，误导用户以为权重没下载。 ✅ **已实施并修正**
   → `model_manager.py` 加 `_precision_fits_vram()` / `_min_footprint_gb()`，按「全量 BlockSwap 下界
   ×1.15 余量」做 **fail-open** 判定（遵循 §5 资源门禁铁律 / GOTCHAS #99/#100）：拒绝条件必须是
   "最大降级组合仍放不下"，而非"当前配置超预算"。实测 3B nvfp4 全量 BlockSwap 下界 ≈9.0 GB、门槛
   ≈10.35 GB，12 GB 卡放得下；同时把回退失败信息拆成「配置但未下载」「磁盘有文件但显存不足被排除」
   两类，不再误导。验证：12 GB 卡 + 仅 nvfp4 权重 → `found='nvfp4'`、`vram_rejected=[]`（GOTCHAS #108）。

5. **修正 OOM 降级链**：`bad_case_retry.py:321` 的 `fallback_chain` 里
   `fp16 → nvfp4` 不省显存，应改为 `fp16 → fp8`，或只保留真正改变驻留档位的精度。 ✅ **已实施**
   → 改用 `gpu_utils.precision_saves_vram`，只在「驻留档位确实变小（实际只有 fp8）」时降级（GOTCHAS #105/#108）。

6. **`blocks_to_swap` 按显存自动取值**：12 GB 卡不要默认 0。 ✅ **已实施**
   → `gpu_utils.recommend_blocks_to_swap()` 按缺口「够用即可」反推块数（换 4 块不再和换 28 块估出同样的收益），
   削减量改为按 `换出块数 / 总块数` 线性（`blockswap_reduction_gb`）。

7. **`double_res` 加最大像素上限**（例如 ≤ 4 MP），避免 3344×1882 这种输出直接把 12 GB 卡打爆。 ✅ **已实施**
   → `routes/restore/common.py` 加 `DOUBLE_RES_MAX_PIXELS = 8_000_000`，超限按比例回缩短边并告警（GOTCHAS #109）。

8. **清理空转的 GroupNormAccumulator / TiledVAEHook**（其日志 `groupnorm_accum=True` / `gaussian_blend=True` 属误导）。 ✅ **已实施**
   → `_vae_pipeline._vae_decode` 移除死路径与误导日志；两个类 docstring 标注「未接线，需 model_lib 侧加回调」（GOTCHAS #110）。

---

## 五、复现脚本

保留在 `experiments/` 下，可自行重跑：

```bash
# 各 tile size 的耗时 / 峰值显存（空显存基线）
./.venv/Scripts/python.exe experiments/_diag_vae_bench.py

# A/B：预占不同显存后，同一解码的耗时
DIT_GB=0    ./.venv/Scripts/python.exe -u experiments/_diag_ab.py
DIT_GB=6.31 ./.venv/Scripts/python.exe -u experiments/_diag_ab.py
```
