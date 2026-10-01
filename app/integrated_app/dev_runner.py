#!/usr/bin/env python3
"""开发模式 ASGI 入口（供 `uvicorn app.integrated_app.dev_runner:app --reload` 使用）。

为什么存在：uvicorn 的 `--reload` 只接受 `module:app` 形式的 import string，而
`app_server` 刻意不提供模块级 `app`（ASGI 应用在 `create_app(config)` 里按当前
config 现场构造，模块级只有 `PROJECT_ROOT / LOG_FORMAT / logger`）。此前
`start.bat --dev` 直接写 `app_server:app`，uvicorn 找不到属性起不来
（GOTCHAS #149）。本模块只做这一件事：import 时构造好 `app` 交给 uvicorn，
不添加任何其他行为；生产/常规启动路径不受影响（clean_launch / start_portable
直跑 `__main__`，不经过这里）。

用法（start.bat --dev 同款）：
    python -X utf8 -m uvicorn app.integrated_app.dev_runner:app \
        --host 127.0.0.1 --port 7870 --workers 1 --reload
"""

import os
import sys

# 确保项目根目录在 sys.path 中（reload 子进程不继承 -m 的 cwd 注入保证）
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app.integrated_app.app_server import create_app, setup_logging  # noqa: E402
from app.integrated_app.config import load_config  # noqa: E402

_config = load_config()
setup_logging(_config)
app = create_app(_config)
