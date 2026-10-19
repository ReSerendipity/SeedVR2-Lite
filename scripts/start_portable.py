#!/usr/bin/env python3
"""
SeedVR2 便携版启动脚本（供 Tauri 壳调用）

与 app_server.py 的 main() 区别：
- 支持 --port / --host 命令行参数（由 Tauri 壳传入随机端口）
- 不自动打开浏览器（由 Tauri WebView 加载）
- 日志输出到 stdout/stderr（Tauri 壳重定向到文件）
"""

import argparse
import os
import sys

# 确保项目根目录在 sys.path 中。
# 开发态：本文件位于 <repo_root>/scripts/start_portable.py，app/ 包在上一级。
# 打包态：本文件位于 <app_dir>/start_portable.py（app_dir 内即含 app/ Python 包）。
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if os.path.isfile(os.path.join(SCRIPT_DIR, "app", "integrated_app", "app_server.py")):
    PROJECT_ROOT = SCRIPT_DIR
else:
    PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# 环境隔离（桌面端迁移 B-4，移植自 clean_launch.setup_isolated_env）：
# 清除可能指向其他 Python 的环境变量、移除系统/用户包路径，防止桌面端
# 在用户机器上加载错解释器标准库或第三方包（壳只设了 PYTHONNOUSERSITE）。
for _var in ("PYTHONHOME", "PYTHONSTARTUP"):
    os.environ.pop(_var, None)
sys.path = [
    p
    for p in sys.path
    if not any(exclude in p.lower() for exclude in ("\\appdata\\", "\\program files\\", "\\programdata\\"))
]
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def main():
    parser = argparse.ArgumentParser(description="SeedVR2 便携版启动器")
    parser.add_argument("--port", type=int, default=None, help="监听端口（默认从 config.yaml 读取）")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="监听地址")
    parser.add_argument("--config", type=str, default=None, help="配置文件路径")
    args = parser.parse_args()

    import uvicorn

    from app.integrated_app.app_server import create_app, load_config, setup_logging

    # 加载配置
    config = load_config(args.config) if args.config else load_config()

    # 命令行参数覆盖配置
    host = args.host
    port = args.port or config.get("server", {}).get("port", 7870)

    # 同步更新配置
    config.setdefault("server", {})["host"] = host
    config["server"]["port"] = port

    # 将动态端口加入 CORS 白名单
    origins = config["server"].setdefault("allowed_origins", [])
    origin = f"http://{host}:{port}"
    if origin not in origins:
        origins.append(origin)
    # Tauri WebView 的 origin
    tauri_origin = "http://tauri.localhost"
    if tauri_origin not in origins:
        origins.append(tauri_origin)

    # 禁用自动打开浏览器（Tauri 壳负责显示）
    config.setdefault("server", {})["auto_open_browser"] = False

    # 日志配置
    log_level = config.get("logging", {}).get("level", "INFO")
    setup_logging(config)

    # 创建应用
    app = create_app(config)

    print(f"SeedVR2 启动中... http://{host}:{port}", flush=True)
    print(f"工作目录: {PROJECT_ROOT}", flush=True)

    # 启动 uvicorn（不启用 reload，不打开浏览器）
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=log_level.lower(),
        reload=False,
    )


if __name__ == "__main__":
    main()
