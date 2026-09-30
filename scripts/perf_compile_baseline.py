#!/usr/bin/env python3
"""torch.compile 分阶段性能基线（W5-4 / 任务 #26）。

量三件事，且必须分开看，混在一起就会得出误导性结论：

- **首包**：一次进程内第一次推理的耗时。跑前会清 inductor 磁盘缓存，所以它就是
  "用户刚把开关打开"的体感（含冷编译）。
- **稳态**：同进程内第 2..N 次的中位数。
- **数值一致性**：各组输出是否还是同一张图。

设计约束
--------
1. **每个组合一个独立子进程**：torch.compile 的编译状态是进程级的，同进程内切档会互相污染。
2. **每组跑前清 inductor 磁盘缓存**，否则后跑的组白捡前面组编好的产物，四组数字不可比。
   清理范围只有 ``.torch_cache/inductor``（可再生缓存），不碰权重与用户输出。
3. **固定 seed**：``config.yaml`` 的 ``inference.seed`` 是 ``-1``（每次随机）。不钉死的话
   各组产出的根本不是同一张图，一致性比较无从谈起。
4. **一致性用数值指纹，不用哈希相等**：bf16 + inductor 的内核选择可能带来合法微小差异，
   拿 sha256 相等当"编译不改变数值"的判据会产生假红。这里报 sha + 均值 + 标准差。
5. **关掉颜色校正**（``color_correction=none``）：LAB 校正会把色差吸收掉，掩盖编译的数值影响。
6. 只读：编译档位在本进程内存里覆盖，**不改 config.yaml**（它属禁区）。

架构前提（决定读法）：本项目是四阶段销毁架构，每次推理都会重新加载并销毁 DiT/VAE，
所以"每次"里本来就含模型加载时间，编译收益会被稀释。这不是测量误差，是架构现状。

用法
----
    python scripts/perf_compile_baseline.py --all                  # 四组依次跑并出报告
    python scripts/perf_compile_baseline.py --combo dit --runs 3   # 只跑一组，打印 JSON
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import datetime as dt
import hashlib
import json
import os
import statistics
import subprocess  # nosec B404（只调用本脚本自身，参数列表，无 shell）
import sys
import time
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        with contextlib.suppress(OSError, ValueError):
            _stream.reconfigure(encoding="utf-8", errors="replace")

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 必须与 app/clean_launch.py 同源，否则量到的不是用户实际使用的缓存路径
INDUCTOR_CACHE = ROOT / ".torch_cache" / "inductor"
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("TORCHINDUCTOR_CACHE_DIR", str(INDUCTOR_CACHE))

REPORT_DIR = ROOT / "docs" / "reports"
DEFAULT_INPUT = ROOT / "data" / "test" / "test-inputs" / "grace_hopper.jpg"
DEFAULT_RESOLUTION = 512
FIXED_SEED = 20260922
MODEL_SIZE = "3b"
PRECISION = "fp8"

COMBOS: dict[str, tuple[bool, bool]] = {
    "off": (False, False),
    "dit": (True, False),
    "vae": (False, True),
    "both": (True, True),
}


def build_compile_config(dit_on: bool, vae_on: bool) -> dict:
    """构造分阶段 torch_compile 配置。

    两组都用 fullgraph=False + mode=default：``blockswap.py`` 刻意用 ``torch.compiler.disable``
    标记了交换计时区域（``wrapped_io_forward`` → ``_get_swap_start_time``），DiT 整图要求会
    **必然抛错而不是降级** —— 实测 fullgraph=True 时 dit / both 两组直接 success=False。
    reduce-overhead 走 CUDA graphs，与跨 CPU/GPU 搬权重的 BlockSwap 是第二层冲突，不作默认。
    """
    return {
        "dit": {
            "enabled": dit_on,
            "mode": "default",
            "backend": "inductor",
            "fullgraph": False,
            "dynamic": False,
        },
        "vae": {"enabled": vae_on, "mode": "default", "backend": "inductor", "fullgraph": False, "dynamic": False},
    }


def clear_inductor_cache() -> int:
    """删除可再生的 inductor 磁盘缓存，返回清理条目数。只碰 .torch_cache/inductor。"""
    if not INDUCTOR_CACHE.is_dir():
        return 0
    import shutil

    count = sum(1 for _ in INDUCTOR_CACHE.rglob("*"))
    shutil.rmtree(INDUCTOR_CACHE, ignore_errors=True)
    return count


def _fingerprint(path: Path) -> str:
    """sha 前 16 位 + 像素均值/标准差；比裸哈希更适合判断"编译有没有改变数值"。"""
    import numpy as np
    from PIL import Image

    raw = path.read_bytes()
    arr = np.asarray(Image.open(path).convert("RGB"), dtype=np.float64)
    return f"{hashlib.sha256(raw).hexdigest()[:16]}|mean={arr.mean():.3f}|std={arr.std():.3f}"


def assert_compile_engages() -> None:
    """前置自检：确认 torch.compile 在本环境**真的**可用，否则拒绝测基线。

    为什么不自己拿 nn.Linear 探一下就行（曾这么做）：简单算子 inductor 能退到 C++ 路径，
    于是"探测通过"，而真实 3B DiT 首帧照样抛 ``Cannot find a working triton installation``。
    复用 :func:`compile_support` 才能同时覆盖 UTF-8 模式与 triton 两道前提。

    缺这一条会怎样：四组"编译 vs 不编译"其实跑的是同一条 eager 路径，报告却长得像一份
    正常的"该特性无收益"结论 —— 探测链路坏了却输出得像结论，是最贵的失败模式。
    """
    from app.integrated_app.optimization.gpu.vram_toolchain import compile_support

    support = compile_support(refresh=True)
    if not support.get("available"):
        raise RuntimeError(
            "torch.compile 在本环境不可用（"
            + str(support.get("reason") or "未知原因")
            + "）。此时四组组合会退化成同一条 eager 路径跑四遍，"
            "测不出任何关于编译收益的结论，因此拒绝出报告。"
            "需同时满足：进程运行在 UTF-8 模式（否则 inductor 用 GBK 读文件崩），"
            "且 CUDA 上已装 triton（真实 DiT 编译必需）。"
        )


def run_combo(combo: str, runs: int, resolution: int, input_path: Path) -> dict:
    """在当前进程内跑一个组合，返回测量结果。"""
    import torch

    from app.integrated_app.config import load_config
    from app.integrated_app.engines.seedvr2_engine import ImageInferenceConfig
    from app.integrated_app.model_manager import ModelManager
    from app.integrated_app.model_registry import model_registry

    dit_on, vae_on = COMBOS[combo]
    if dit_on or vae_on:
        assert_compile_engages()
    config = load_config()
    config.setdefault("inference", {})["torch_compile"] = build_compile_config(dit_on, vae_on)
    config["inference"]["seed"] = FIXED_SEED
    config["inference"]["resolution"] = resolution

    out_dir = ROOT / "data" / "perf_out" / combo
    out_dir.mkdir(parents=True, exist_ok=True)
    # 必须先清掉上一轮的产物：引擎对重名输出会加 `_1` 后缀，于是"按 perf_<combo>_<i>.png 找文件"
    # 会指纹到上一轮的旧图，产出看着完整实则混了两次运行的数据。
    for stale in out_dir.glob(f"perf_{combo}_*"):
        with contextlib.suppress(OSError):
            stale.unlink()

    load_result = asyncio.run(ModelManager(config).load_model(model_size=MODEL_SIZE, precision=PRECISION))
    engine = model_registry.get_engine()
    if engine is None:
        return {"combo": combo, "error": f"模型已加载但取不到引擎实例；load_result={load_result}"}

    durations: list[float] = []
    fingerprints: list[str] = []
    peaks: list[int] = []

    for i in range(runs):
        cfg = ImageInferenceConfig(
            dit_model=f"{MODEL_SIZE}_{PRECISION}",
            seed=FIXED_SEED,
            resolution=resolution,
            color_correction="none",
        )
        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
        started = time.perf_counter()
        result = asyncio.run(
            engine.infer_image(
                image_path=str(input_path),
                output_dir=str(out_dir),
                config=cfg,
                output_name=f"perf_{combo}_{i}.png",
            )
        )
        durations.append(round(time.perf_counter() - started, 2))
        if not getattr(result, "success", False):
            return {"combo": combo, "error": f"第 {i + 1} 次推理未成功: {result}", "durations": durations}
        # 以引擎回报的路径为准，别靠文件名猜（重名时它会自己加 `_1` 后缀）
        reported = getattr(result, "output_path", None)
        produced = Path(reported) if reported else out_dir / f"perf_{combo}_{i}.png"
        if not produced.exists():
            fresh = sorted(out_dir.glob(f"perf_{combo}_{i}*"), key=lambda p: p.stat().st_mtime)
            if not fresh:
                return {"combo": combo, "error": f"推理报成功但找不到输出文件 {produced}", "durations": durations}
            produced = fresh[-1]
        fingerprints.append(_fingerprint(produced))
        peaks.append(int(torch.cuda.max_memory_reserved() / 2**20) if torch.cuda.is_available() else 0)

    return {
        "combo": combo,
        "compile_flags": {"dit": dit_on, "vae": vae_on},
        "load_status": load_result.get("status"),
        "precision_loaded": load_result.get("precision"),
        "durations_s": durations,
        "cold_s": durations[0] if durations else None,
        "steady_s": round(statistics.median(durations[1:]), 2) if len(durations) > 1 else None,
        "peak_vram_mb": max(peaks) if peaks else 0,
        "fingerprints": fingerprints,
        "output_dir": str(out_dir),
        "model": {"size": MODEL_SIZE, "precision": PRECISION, "resolution": resolution, "seed": FIXED_SEED},
    }


def driver(runs: int, resolution: int, input_path: Path, keep_cache: bool) -> list[dict]:
    """逐组合各起一个子进程跑，收集 JSON。"""
    results: list[dict] = []
    for combo in COMBOS:
        if not keep_cache:
            freed = clear_inductor_cache()
            print(f"[driver] {combo}: 已清 inductor 缓存（{freed} 项）以保证组间可比", flush=True)
        cmd = [
            sys.executable,
            # 子进程必须同样跑在 UTF-8 模式：否则 inductor 用 GBK 读文件崩掉、编译静默回退，
            # 量出来的又是"四组无差异"的假结论（本机踩过，见 docs/reports/compile_perf_*.md——维护者本地目录，未随仓库分发）。
            "-X",
            "utf8",
            str(SCRIPT_DIR / "perf_compile_baseline.py"),
            "--combo",
            combo,
            "--runs",
            str(runs),
            "--resolution",
            str(resolution),
            "--input",
            str(input_path),
        ]
        started = time.time()
        print(f"[driver] {combo} 开跑 @ {time.strftime('%H:%M:%S')}", flush=True)
        proc = subprocess.run(  # nosec B603（固定解释器 + 参数列表）
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3600,
            check=False,
        )
        print(f"[driver] {combo} 结束，{time.time() - started:.0f}s rc={proc.returncode}", flush=True)
        payload = None
        for line in reversed((proc.stdout or "").splitlines()):
            if line.startswith("{"):
                with contextlib.suppress(ValueError):
                    payload = json.loads(line)
                    break
        if payload is None:
            tail = (proc.stderr or proc.stdout or "").strip()[-500:]
            payload = {"combo": combo, "error": f"子进程未产出 JSON（rc={proc.returncode}）：{tail}"}
            print(f"[driver] {payload['error']}", file=sys.stderr, flush=True)
        results.append(payload)
    return results


def write_report(results: list[dict], meta: dict) -> Path:
    today = dt.date.today().strftime("%Y%m%d")
    path = REPORT_DIR / f"compile_perf_{today}.md"
    baseline = next((r for r in results if r.get("combo") == "off" and not r.get("error")), {})
    bcold, bsteady = baseline.get("cold_s"), baseline.get("steady_s")

    def delta(value: object, base: object) -> str:
        if isinstance(value, (int, float)) and isinstance(base, (int, float)) and base:
            gain = (base - value) / base * 100.0
            return f"{value - base:+.1f}s ({gain:+.0f}%)"
        return "—"

    lines = [
        f"# torch.compile 分阶段性能基线 compile_perf_{today}",
        "",
        f"- 生成时间：{dt.datetime.now().isoformat(timespec='seconds')}",
        f"- GPU：{meta.get('gpu', '?')} ｜ 显存 {meta.get('vram_gb', '?')} GB ｜ 物理内存可用 {meta.get('ram_free_gb', '?')} GB",
        f"- 样本：`{meta.get('input')}` ｜ 目标长边 {meta.get('resolution')} ｜ **seed 固定 {meta.get('seed')}** ｜ `color_correction=none`",
        f"- 每组跑前清 inductor 缓存：{not meta.get('keep_cache')} ｜ 每组 {meta.get('runs')} 次推理",
        "",
        "> 读法提醒：本项目是四阶段销毁架构，**每次推理都会重新加载并销毁 DiT/VAE**，",
        "> 所以下表「每次」里本来就含模型加载时间，编译收益被稀释是架构现状而非测量误差。",
        "",
        "| 组合 | DiT编 | VAE编 | 首包(s) | 稳态(s) | Δ首包 vs off | Δ稳态 vs off | 峰值显存(MiB) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        if r.get("error"):
            lines.append(f"| `{r.get('combo')}` | — | — | — | — | — | — | **ERROR** `{str(r['error'])[:80]}` |")
            continue
        flags = r["compile_flags"]
        lines.append(
            "| `{combo}` | {dit} | {vae} | {cold} | {steady} | {dc} | {ds} | {peak} |".format(
                combo=r["combo"],
                dit=flags["dit"],
                vae=flags["vae"],
                cold=r.get("cold_s"),
                steady=r.get("steady_s"),
                dc=delta(r.get("cold_s"), bcold),
                ds=delta(r.get("steady_s"), bsteady),
                peak=r.get("peak_vram_mb"),
            )
        )

    lines += ["", "## 逐次耗时与数值指纹", ""]
    for r in results:
        if r.get("error"):
            lines.append(f"- `{r.get('combo')}`：ERROR —— {r['error']}")
            continue
        lines.append(f"- `{r['combo']}` durations={r['durations_s']} 峰值显存={r['peak_vram_mb']} MiB")
        for idx, fp in enumerate(r["fingerprints"], 1):
            lines.append(f"  - 第 {idx} 次：`{fp}`")

    same = [r for r in results if not r.get("error") and r.get("fingerprints")]
    distinct = {fp for r in same for fp in r["fingerprints"][1:]}
    lines += [
        "",
        "## 判读要点",
        "",
        f"- 各组第 2..N 次共产生 {len(distinct)} 个不同指纹。编译不该改变数值；"
        "若只有 off 与编译组之间不同且差异在 bf16 容差内，属可接受，需在结论里量化说明。",
        "- 稳态无收益就如实记无收益，不为此改产品默认值去凑。",
        "- 首包受 inductor 磁盘缓存支配：见 KNOWN_ISSUES #12（未持久化时重启仍需约 100s 重编）。",
        "- 本表只覆盖 3B/fp8（本机唯一在盘权重）；7B 量级需先解掉 model_lib/dit 断链（任务 #37）。",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return path


def _memory_meta() -> dict:
    import ctypes
    import ctypes.wintypes as wt

    class Status(ctypes.Structure):
        _fields_ = [
            ("dwLength", wt.DWORD),
            ("dwMemoryLoad", wt.DWORD),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    # dwLength 必须等于 MEMORYSTATUSEX 的真实大小；虚报会让 GlobalMemoryStatusEx
    # 直接失败并把结构留在零值上 —— 表现为"可用内存 0.0 GB"这种看着像结论的假数据。
    expected = 8 + 7 * 8
    s = Status()
    s.dwLength = ctypes.sizeof(Status)
    if s.dwLength != expected:
        return {"ram_free_gb": "?"}
    with contextlib.suppress(Exception):
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s))
        return {"ram_free_gb": round(s.ullAvailPhys / 2**30, 2)}
    return {"ram_free_gb": "?"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="torch.compile 分阶段性能基线（详见模块 docstring）")
    parser.add_argument("--combo", choices=sorted(COMBOS), help="只跑一个组合（子进程模式）")
    parser.add_argument("--all", action="store_true", help="四组依次以子进程跑并出报告")
    parser.add_argument("--runs", type=int, default=3, help="每组推理次数（第 1 次=首包，2..N=稳态）")
    parser.add_argument("--resolution", type=int, default=DEFAULT_RESOLUTION)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--keep-cache", action="store_true", help="不清 inductor 缓存（会破坏组间可比性）")
    args = parser.parse_args(argv)

    if not args.input.exists():
        print(f"[错误] 输入样本不存在：{args.input}", file=sys.stderr)
        return 2

    if args.combo and not args.all:
        result = run_combo(args.combo, args.runs, args.resolution, args.input)
        print(json.dumps(result, ensure_ascii=False))
        return 1 if result.get("error") else 0

    if not args.all:
        parser.error("需要 --all 或 --combo")

    import torch

    meta = {
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "无 CUDA",
        "vram_gb": (
            round(torch.cuda.get_device_properties(0).total_memory / 2**30, 1) if torch.cuda.is_available() else 0
        ),
        "input": str(args.input),
        "resolution": args.resolution,
        "seed": FIXED_SEED,
        "keep_cache": args.keep_cache,
        "runs": args.runs,
        **_memory_meta(),
    }
    results = driver(args.runs, args.resolution, args.input, args.keep_cache)
    path = write_report(results, meta)
    print(f"[written] {path}")
    return 0 if not any(r.get("error") for r in results) else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
