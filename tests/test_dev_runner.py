"""dev_runner 与 start.bat --dev 的契约锁（GOTCHAS #149 回归防线）。

uvicorn `--reload` 只接受 `module:app` import string，而 app_server 刻意不提供
模块级 `app`。start.bat --dev 必须指向 dev_runner（import 时构造 app 的专用入口）；
两条断言分别锁住「指向正确」与「dev_runner 真能给出可用的 ASGI 应用」。
"""

import importlib
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_start_bat_dev_targets_dev_runner():
    bat = (PROJECT_ROOT / "start.bat").read_text(encoding="utf-8", errors="ignore")
    # 指向 dev_runner（uvicorn --reload 的 import string）
    assert "app.integrated_app.dev_runner:app" in bat
    # 且不再引用 app_server:app（历史上写错的目标，uvicorn 找不到属性）
    assert not re.search(r"app_server:app", bat)


def test_dev_runner_exposes_asgi_app():
    mod = importlib.import_module("app.integrated_app.dev_runner")
    app = mod.app
    # FastAPI 实例且构造期路由已注册（create_app 完成构造的最低判据；
    # API 路由由 lifespan 启动期的自动发现注册，这里以页面路由为凭据）
    from fastapi import FastAPI

    assert isinstance(app, FastAPI)
    paths = [getattr(r, "path", None) for r in app.routes]
    assert len(paths) > 0
    assert "/system-status" in paths
