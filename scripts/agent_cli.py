#!/usr/bin/env python3
"""SeedVR2 Agent CLI — 自动化接口契约。

参考 ComfyUI-Mie-Package-Launcher 的 Agent CLI 设计，
提供机器可读的 JSON 输出和标准化退出码，
供 AI Agent / CI / 桌面壳自动化调用。

退出码契约：
    0  — 成功
    1  — 通用错误
    2  — 无效参数
    3  — 依赖缺失（如 torch/nunchaku 未安装）
    4  — 模型/资源缺失
    5  — 配置错误
    10 — GPU 不可用
    20 — 端口被占用
    30 — 完整性校验失败

用法::

    python scripts/agent_cli.py status --json
    python scripts/agent_cli.py check --json
    python scripts/agent_cli.py models --json
    python scripts/agent_cli.py launch --port 7870 --json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).parent.parent

# 退出码契约
EXIT_OK = 0
EXIT_GENERAL_ERROR = 1
EXIT_INVALID_ARGS = 2
EXIT_MISSING_DEPENDENCY = 3
EXIT_MISSING_RESOURCE = 4
EXIT_CONFIG_ERROR = 5
EXIT_GPU_UNAVAILABLE = 10
EXIT_PORT_IN_USE = 20
EXIT_INTEGRITY_FAILED = 30


@dataclass
class CLIResult:
    """CLI 执行结果（JSON 输出结构）。"""

    success: bool
    exit_code: int
    command: str
    message: str
    data: dict[str, Any] | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)


def _emit(result: CLIResult, use_json: bool) -> None:
    """输出结果（JSON 或纯文本）。"""
    if use_json:
        print(result.to_json(), file=_result_stream())
    else:
        status = "OK" if result.success else "FAIL"
        print(f"[{status}] {result.message}")
        if result.data:
            for k, v in result.data.items():
                print(f"  {k}: {v}")


def cmd_status(args: argparse.Namespace) -> CLIResult:
    """检查系统状态。"""
    data: dict[str, Any] = {
        "project_root": str(PROJECT_ROOT),
        "python_version": sys.version,
    }

    # 检查 PyTorch
    try:
        import torch

        data["torch_version"] = torch.__version__
        data["cuda_available"] = torch.cuda.is_available()
        if torch.cuda.is_available():
            data["cuda_device"] = torch.cuda.get_device_name(0)
            data["cuda_capability"] = (
                f"{torch.cuda.get_device_capability(0)[0]}.{torch.cuda.get_device_capability(0)[1]}"
            )
    except ImportError:
        return CLIResult(
            success=False,
            exit_code=EXIT_MISSING_DEPENDENCY,
            command="status",
            message="PyTorch 未安装",
            data=data,
        )

    # 检查 nunchaku
    try:
        import nunchaku  # noqa: F401

        data["nunchaku_available"] = True
    except ImportError:
        data["nunchaku_available"] = False

    # 检查 SageAttention
    try:
        import sageattention  # noqa: F401

        data["sageattention_available"] = True
    except ImportError:
        data["sageattention_available"] = False

    # 检查模型文件
    models_dir = PROJECT_ROOT / "models"
    data["models_dir_exists"] = models_dir.exists()
    if models_dir.exists():
        data["model_files"] = [f.name for f in models_dir.glob("*.safetensors")][:5]

    return CLIResult(
        success=True,
        exit_code=EXIT_OK,
        command="status",
        message="系统状态检查完成",
        data=data,
    )


def cmd_check(args: argparse.Namespace) -> CLIResult:
    """运行完整性/配置检查。"""
    data: dict[str, Any] = {}
    errors: list[str] = []

    # 检查 config.yaml
    config_path = PROJECT_ROOT / "config.yaml"
    data["config_exists"] = config_path.exists()
    if not config_path.exists():
        errors.append("config.yaml 不存在")

    # 检查核心模块可导入
    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from app.integrated_app.optimization.roadmap import get_overall_statistics

        stats = get_overall_statistics()
        data["roadmap_stats"] = stats
    except Exception as e:
        errors.append(f"roadmap 导入失败: {e}")

    # 检查完整性 manifest
    manifest_path = PROJECT_ROOT / "security" / "integrity_manifest.json"
    data["integrity_manifest_exists"] = manifest_path.exists()

    success = len(errors) == 0
    return CLIResult(
        success=success,
        exit_code=EXIT_OK if success else EXIT_INTEGRITY_FAILED,
        command="check",
        message="检查通过" if success else f"发现 {len(errors)} 个问题",
        data={**data, "errors": errors} if errors else data,
    )


def cmd_models(args: argparse.Namespace) -> CLIResult:
    """列出可用模型。"""
    data: dict[str, Any] = {"models": []}

    models_dir = PROJECT_ROOT / "models"
    if models_dir.exists():
        for f in sorted(models_dir.glob("**/*.safetensors")):
            rel = f.relative_to(PROJECT_ROOT)
            size_mb = f.stat().st_size / (1024 * 1024)
            data["models"].append(
                {
                    "path": str(rel),
                    "size_mb": round(size_mb, 1),
                }
            )

    return CLIResult(
        success=True,
        exit_code=EXIT_OK,
        command="models",
        message=f"发现 {len(data['models'])} 个模型文件",
        data=data,
    )


def cmd_launch(args: argparse.Namespace) -> CLIResult:
    """启动服务（检查端口后启动）。"""
    import socket

    # 检查端口
    port = args.port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            port_free = True
        except OSError:
            port_free = False

    if not port_free:
        return CLIResult(
            success=False,
            exit_code=EXIT_PORT_IN_USE,
            command="launch",
            message=f"端口 {port} 已被占用",
            data={"port": port},
        )

    return CLIResult(
        success=True,
        exit_code=EXIT_OK,
        command="launch",
        message=f"端口 {port} 可用，可以启动服务",
        data={"port": port, "host": "127.0.0.1"},
    )


def cmd_restore(args: argparse.Namespace) -> CLIResult:
    """图像超分：通过已运行的服务提交任务并等待结果。

    SeedVR2 的推理编排含显存预检 / 精度降级 / 坏案例重试，CLI 直调引擎会绕过
    这些保护，因此 restore 默认走 HTTP 复用常驻服务（与 VOSR2-Lite 的
    ``--server-url`` 同构，只是这里推理只有这一条路）。
    """
    import os
    import time

    import httpx

    server = args.server_url.rstrip("/")
    source = args.input
    if not os.path.isfile(source):
        return CLIResult(False, EXIT_MISSING_RESOURCE, "restore", f"输入文件不存在: {source}")
    os.makedirs(args.output_dir, exist_ok=True)

    try:
        with httpx.Client(base_url=server, timeout=30.0) as client:
            with open(source, "rb") as fh:
                resp = client.post(
                    "/api/restore/",
                    files={"file": (os.path.basename(source), fh, "image/png")},
                    data={
                        "model_size": args.model_size,
                        "seed": str(args.seed),
                        "output_format": args.output_format,
                    },
                )
            resp.raise_for_status()
            payload = resp.json()
            if payload.get("success") is False:
                return CLIResult(False, EXIT_GENERAL_ERROR, "restore", str(payload.get("error")))
            data = payload.get("data") or payload
            task_id = data.get("task_id", "")
            if not task_id:
                return CLIResult(False, EXIT_GENERAL_ERROR, "restore", "服务未返回 task_id")

            deadline = time.time() + args.timeout
            while time.time() < deadline:
                result_resp = client.get(f"/api/restore/{task_id}/result")
                result_resp.raise_for_status()
                info = result_resp.json()
                info = info.get("data") or info
                status = info.get("status", "")
                if status == "completed":
                    out_path = info.get("output_path", "")
                    target = os.path.join(args.output_dir, f"{os.path.splitext(os.path.basename(source))[0]}.png")
                    if out_path and os.path.isfile(out_path) and os.path.abspath(out_path) != os.path.abspath(target):
                        import shutil

                        shutil.copy2(out_path, target)
                        out_path = target
                    return CLIResult(
                        True,
                        EXIT_OK,
                        "restore",
                        "修复完成",
                        {"output": out_path, "task_id": task_id, "server": server},
                    )
                if status in ("failed", "cancelled"):
                    return CLIResult(
                        False,
                        EXIT_GENERAL_ERROR,
                        "restore",
                        f"任务 {status}: {info.get('error', '')}",
                    )
                time.sleep(0.5)
            return CLIResult(False, EXIT_GENERAL_ERROR, "restore", f"任务 {task_id} 超时")
    except Exception as exc:
        return CLIResult(False, EXIT_GENERAL_ERROR, "restore", f"调用服务失败: {exc}")


_ORIGINAL_STDOUT = None


def _begin_json_mode() -> None:
    """把第三方 print 隔离到 stderr，JSON 结果由 _emit 写回原始 stdout。"""
    global _ORIGINAL_STDOUT
    if _ORIGINAL_STDOUT is None:
        _ORIGINAL_STDOUT = sys.stdout
        sys.stdout = sys.stderr


def _result_stream():
    return _ORIGINAL_STDOUT if _ORIGINAL_STDOUT is not None else sys.stdout


def main(argv: list[str] | None = None) -> int:
    # argv=None 时用 sys.argv（正常 CLI）；显式传参供进程内测试调用。
    _argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(
        description="SeedVR2 Agent CLI — 自动化接口契约",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
退出码契约:
  0  成功
  1  通用错误
  2  无效参数
  3  依赖缺失
  4  资源缺失
  5  配置错误
  10 GPU 不可用
  20 端口被占用
  30 完整性校验失败
        """,
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 格式输出")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # status
    p_status = subparsers.add_parser("status", help="检查系统状态")
    p_status.add_argument("--json", action="store_true", help="以 JSON 格式输出")

    # check
    p_check = subparsers.add_parser("check", help="运行完整性/配置检查")
    p_check.add_argument("--json", action="store_true", help="以 JSON 格式输出")

    # models
    p_models = subparsers.add_parser("models", help="列出可用模型")
    p_models.add_argument("--json", action="store_true", help="以 JSON 格式输出")

    # launch
    launch_parser = subparsers.add_parser("launch", help="检查端口并准备启动")
    launch_parser.add_argument("--port", type=int, default=7870, help="端口号（默认 7870）")
    launch_parser.add_argument("--json", action="store_true", help="以 JSON 格式输出")

    # restore：图像超分（走已运行的服务，保留显存预检/精度降级/重试保护）
    p_restore = subparsers.add_parser("restore", help="图像超分（需先启动 Web 服务）")
    p_restore.add_argument("-i", "--input", required=True, help="输入图片路径")
    p_restore.add_argument("-o", "--output-dir", default="outputs", help="输出目录")
    p_restore.add_argument("--server-url", default="http://127.0.0.1:7870", help="服务地址")
    p_restore.add_argument("--model-size", default="3b", help="模型规格（默认 3b）")
    p_restore.add_argument("--seed", type=int, default=12345, help="随机种子")
    p_restore.add_argument("--output-format", default="png", help="输出格式")
    p_restore.add_argument("--timeout", type=float, default=3600, help="任务超时秒数")

    # --json 任意位置生效：顶层与各子命令各自声明，取并集
    use_json = "--json" in _argv
    if use_json:
        _argv = [a for a in _argv if a != "--json"]
        _begin_json_mode()
    args = parser.parse_args(_argv)

    commands = {
        "status": cmd_status,
        "check": cmd_check,
        "models": cmd_models,
        "launch": cmd_launch,
        "restore": cmd_restore,
    }

    try:
        result = commands[args.command](args)
    except Exception as e:
        result = CLIResult(
            success=False,
            exit_code=EXIT_GENERAL_ERROR,
            command=args.command,
            message=f"执行异常: {e}",
        )

    _emit(result, use_json or args.json)
    # 恢复 stdout：进程退出可省略，但便于进程内多次调用 / 测试
    global _ORIGINAL_STDOUT
    if _ORIGINAL_STDOUT is not None:
        sys.stdout = _ORIGINAL_STDOUT
        _ORIGINAL_STDOUT = None
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
