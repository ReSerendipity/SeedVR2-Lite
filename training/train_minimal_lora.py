"""SeedVR2-3B 最小可训练闭环冒烟（12GB 笔记本 GPU）。

设计要点（与 app/integrated_app/engines/seedvr2_engine.py::_load_dit_model 对齐）：
- 3B DiT 来自 model_lib/dit_v2/nadit.py::NaDiT，参数从 configs_3b/config.json 的 dit 段展开。
- fp8_e4m3fn 权重反量化为 fp16，meta 构建 + assign=True 加载。
- 12GB 显存策略：**冻结整个 3B 基座**（不建优化器状态、不保留激活），
  仅在输出侧挂一个可训练低秩 adapter（等价 LoRA：训练参数 MB 级）；
  基座前向在 torch.no_grad() 下运行，反向只经过 adapter。
- 数据：合成 latent（无真实数据集，接口留好；真实数据接入见 build_synthetic_batch 注释）。

运行：
    python training/train_minimal_lora.py --steps 8
"""

from __future__ import annotations

import argparse
import gc
import json
import sys
import time
from pathlib import Path

import torch
import torch.nn as nn

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_dit_config() -> dict:
    import json as _json

    with open(ROOT / "configs_3b" / "config.json") as f:
        return _json.load(f)["dit"]


def build_model(dit_cfg: dict, ckpt_path: Path) -> nn.Module:
    """meta 构建 3B NaDiT 并 assign=True 加载 fp8→fp16 权重。"""
    from safetensors.torch import load_file

    from model_lib.dit_v2.nadit import NaDiT

    num_layers = dit_cfg["num_layers"]
    window_method = dit_cfg.get("window_method")
    if isinstance(window_method, list) and len(window_method) < num_layers:
        window_method = window_method * (num_layers // len(window_method))
        window_method += window_method[: num_layers - len(window_method)]

    sd = load_file(str(ckpt_path), device="cpu")
    n_deq = 0
    for k in list(sd.keys()):
        v = sd[k]
        if isinstance(v, torch.Tensor) and v.dtype == torch.float8_e4m3fn:
            sd[k] = v.to(torch.float16)
            n_deq += 1
    gc.collect()
    print(f"[load] fp8 dequantized tensors: {n_deq}")

    with torch.device("meta"):
        model = NaDiT(
            vid_in_channels=dit_cfg["vid_in_channels"],
            vid_out_channels=dit_cfg["vid_out_channels"],
            vid_dim=dit_cfg["vid_dim"],
            vid_out_norm=dit_cfg.get("vid_out_norm"),
            txt_in_dim=dit_cfg["txt_in_dim"],
            txt_in_norm=dit_cfg.get("txt_in_norm"),
            txt_dim=dit_cfg["txt_dim"],
            emb_dim=dit_cfg["emb_dim"],
            heads=dit_cfg["heads"],
            head_dim=dit_cfg["head_dim"],
            expand_ratio=dit_cfg["expand_ratio"],
            norm=dit_cfg["norm"],
            norm_eps=dit_cfg["norm_eps"],
            ada=dit_cfg["ada"],
            qk_bias=dit_cfg["qk_bias"],
            qk_norm=dit_cfg["qk_norm"],
            patch_size=dit_cfg["patch_size"],
            num_layers=num_layers,
            block_type=dit_cfg["block_type"],
            mm_layers=dit_cfg.get("mm_layers", num_layers),
            mlp_type=dit_cfg.get("mlp_type", "swiglu"),
            msa_type=dit_cfg.get("msa_type"),
            rope_type=dit_cfg.get("rope_type", "mmrope3d"),
            rope_dim=dit_cfg.get("rope_dim", 128),
            window=dit_cfg.get("window"),
            window_method=window_method,
            attention_mode="sdpa",
        )
    info = model.load_state_dict(sd, strict=False, assign=True)
    print(f"[load] missing={len(info.missing_keys)} unexpected={len(info.unexpected_keys)}")
    del sd
    gc.collect()
    for _m in model.modules():
        for _bn, _buf in list(_m.named_buffers(recurse=False)):
            if _buf.is_meta:
                setattr(_m, _bn, torch.zeros_like(_buf, device="cpu"))
    return model


class OutputAdapter(nn.Module):
    """输出侧低秩 adapter：冻结基座上唯一可训练参数（LoRA 等价）。"""

    def __init__(self, out_channels: int, rank: int = 8):
        super().__init__()
        self.down = nn.Linear(out_channels, rank, bias=False)
        self.up = nn.Linear(rank, out_channels, bias=False)
        nn.init.zeros_(self.up.weight)

    def forward(self, vid: torch.Tensor) -> torch.Tensor:
        return vid + self.up(torch.nn.functional.silu(self.down(vid)))


def build_synthetic_batch(device: torch.device, dtype: torch.dtype, grid=(1, 2, 2)):
    """合成 batch：真实数据接入点（当前无训练数据集）。

    形状对齐 _dit_pipeline.py:328-334 的真实调用：
      vid: (L, vid_in_channels=33) 已展平的 patch 序列
      txt: (sum_txt, txt_in_dim=5120)
      vid_shape: list[[t,h,w]]
      txt_shape: list[[n]]
    """
    n_tokens = grid[0] * grid[1] * grid[2]
    vid = torch.randn(n_tokens, 33, device=device, dtype=dtype)
    txt = torch.randn(1, 5120, device=device, dtype=dtype)
    return {
        "vid": vid,
        "txt": txt,
        "vid_shape": torch.tensor([list(grid)], dtype=torch.long, device=device),
        "txt_shape": torch.tensor([[1]], dtype=torch.long, device=device),
        "timestep": torch.tensor([500], device=device, dtype=torch.long),
        "target": torch.randn(n_tokens, 16, device=device, dtype=dtype),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", type=int, default=8)
    ap.add_argument("--ckpt-out", type=str, default="data/checkpoints/minimal_lora_smoke.pt")
    args = ap.parse_args()

    assert torch.cuda.is_available(), "需要 CUDA"
    device = torch.device("cuda")
    dtype = torch.bfloat16

    dit_cfg = load_dit_config()
    ckpt_path = ROOT / "model" / "seedvr2_3b_fp8_e4m3fn.safetensors"
    assert ckpt_path.exists(), f"缺权重 {ckpt_path}"

    t0 = time.time()
    model = build_model(dit_cfg, ckpt_path)
    model = model.to(device=device, dtype=dtype)
    model.eval()
    for p in model.parameters():
        p.requires_grad_(False)
    print(
        f"[load] DiT on GPU in {time.time()-t0:.1f}s; "
        f"peak VRAM after load: {torch.cuda.max_memory_allocated()/1024**2:.0f}MB"
    )

    adapter = OutputAdapter(out_channels=16, rank=8).to(device=device, dtype=dtype)
    n_trainable = sum(p.numel() for p in adapter.parameters() if p.requires_grad)
    print(f"[train] adapter params: {n_trainable/1e3:.1f}K (LoRA 等价, 基座全冻结)")

    opt = torch.optim.AdamW(adapter.parameters(), lr=1e-4, weight_decay=0.0)
    losses = []
    torch.cuda.reset_peak_memory_stats()

    for step in range(args.steps):
        batch = build_synthetic_batch(device, dtype)
        opt.zero_grad(set_to_none=True)
        with torch.no_grad():
            out = model(
                vid=batch["vid"],
                txt=batch["txt"],
                vid_shape=batch["vid_shape"],
                txt_shape=batch["txt_shape"],
                timestep=batch["timestep"],
            ).vid_sample
        pred = adapter(out)
        loss = torch.nn.functional.mse_loss(pred, batch["target"])
        loss.backward()
        opt.step()
        losses.append(float(loss.detach()))
        print(
            f"step {step+1}/{args.steps}  loss={losses[-1]:.6f}  "
            f"vram_peak={torch.cuda.max_memory_allocated()/1024**2:.0f}MB"
        )

    out_path = ROOT / args.ckpt_out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    torch.save({"adapter": adapter.state_dict(), "losses": losses}, out_path)
    result = {
        "steps": args.steps,
        "losses": [round(x, 6) for x in losses],
        "adapter_params": n_trainable,
        "peak_vram_mb": round(torch.cuda.max_memory_allocated() / 1024**2, 1),
        "ckpt": str(out_path),
        "ckpt_bytes": out_path.stat().st_size,
    }
    print("===MINIMAL_TRAIN_RESULT===")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
