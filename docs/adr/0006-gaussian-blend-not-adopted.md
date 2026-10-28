**ADR-0006: 不引入 Gaussian 混合分块（tile 融合由上游 raised-cosine 负责）**

- **状态**: Implemented
- **日期**: 2026-10-28
- **决策者**: 项目维护者（双向吸收计划方向二 · 项 C，经代码复核后调整范围）

---

# 背景与问题

跨仓对照时发现：本仓 `gaussian_blend` / `groupnorm_accumulate` 两个配置键
仍在被读取并打进日志（`seedvr2_engine.py` 构建 `_vae_tiled_config` 处），
而 `_vae_pipeline.py` 的注释明言两者「空转」。表面上像是"用户以为开了
无接缝混合，实际没开"，于是计划里列了一项「参照 VOSR2 的 Gaussian 权重混合
实现真正的分块解码」。

# 复核结论（推翻了上述前提）

**tile 融合一直存在，只是不叫 Gaussian。**

1. 上游 `model_lib/video_vae_v3/modules/attn_video_vae.py::tiled_decode`
   在 overlap 区域用 raised-cosine 斜坡做加权叠加：
   `ramp_cache = 0.5 - 0.5*cos(t*pi)`，并按 `count` 归一化。
   这与 Gaussian 权重混合是**同类方法**（窗函数重叠相加 + 归一化），
   差别只在窗形，不在有无。
2. `_vae_pipeline._vae_decode` 在 `decode_tiled=True` 时确实传
   `vae.decode(tiled=True, tile_size=..., tile_overlap=...)`，即该融合路径
   是**实际生效**的（OOM/NaN 时才回退到非 tiled）。
3. 真正空转的是**另一条**实现：`optimization/inference/vae_tiled_enhance.py`
   里的 `TiledVAEHook` + Gaussian 混合分支。它依赖 VAE 上并不存在的
   `_internal_tile_state` 属性，2026-09-10 已从调用点摘除，全仓无引用。

# 决策

**不实现 Gaussian 混合。** 理由按权重排序：

1. **无收益**：接缝已由上游融合处理；再叠一层 Gaussian 是替换而非补强，
   没有证据表明高斯窗优于余弦窗（两者都是 C¹ 连续的升余弦类窗）。
2. **高风险**：VAE 解码是画质关键路径，替换融合方式会改变全部输出，
   需要大规模真机回归才能确认不劣化。
3. **维护成本**：自研融合必须 monkey-patch 上游 `tiled_decode`，每次上游
   同步都要重新适配；本仓的 `check_upstream_sync` 门禁会持续报警。

**改为修根因**：删掉那两个谎报可配置的键，让日志与配置不再暗示"混合可切换"。

# 实施影响

- `seedvr2_engine.py`：`_vae_tiled_config` 不再包含 `gaussian_blend` /
  `groupnorm_accumulate`（本就没有消费方，行为零变化）。
- `_vae_pipeline.py`：注释补上游文件与窗函数细节，注明"不存在关闭混合的开关"。
- 新增回归测试 `tests/test_vae_blend_semantics.py`：守住 ①引擎不再产出这两个
  键；②解码路径确实以 `tiled=True` + 非零 overlap 调用（融合生效）；
  ③死代码类仍在但不得被引擎引用。
- `vae_tiled_enhance.py` 中的 `TiledVAEHook` / `GaussianBlend` 保留但标注为
  未接线（删除会牵动同文件其他在用工具函数，另开清理批次）。

# 可回滚路径与待验证项

- 纯删除死键 + 注释，回滚成本为零。
- 待验证（用户侧，需真机）：同一张图、同一种子，`decode_tiled=True` 与
  `False` 的输出做 PSNR/目视对比，确认接缝差异不可见。本 ADR 的结论只依赖
  代码路径证据，不依赖该实验；实验用于给"余弦融合够用"补一条量化背书。
