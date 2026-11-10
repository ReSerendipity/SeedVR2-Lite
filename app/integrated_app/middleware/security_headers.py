"""
middleware/security_headers.py — 安全响应头中间件（SeedVR2 适配版）

对应安全评估 M-02：补齐 CSP / X-Content-Type-Options / X-Frame-Options /
Referrer-Policy / Cross-Origin-Opener-Policy 等安全响应头。

SeedVR2 的配置以 dict 形式注入 ``app.state.config``，安全头策略读取
``config["security"]["headers"]``（``enabled`` / ``csp``）。未配置该段时
**安全默认开启**，使用内置默认 CSP —— 与「声明即生效」的安全门禁一致。

置于中间件栈最外层（``add_middleware`` 后添加 = 请求最先执行、响应最后装配），
确保所有响应（含 Basic Auth / CSRF / Rate Limit 产生的 401/403/429）均携带安全头。

默认 CSP 现状（2026-11-09）：``script-src`` 仍留 ``'unsafe-inline'``——模板的内联脚本
全部带 nonce，现代浏览器在 nonce 上下文忽略该回退；``style-src`` 已**不含**
``'unsafe-inline'``（S4 Phase 2 落地，页面内联 style 已清零，运行时改样式走 CSSOM）。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

# 默认 CSP（script-src 保留 unsafe-inline 作无 nonce 回退；style-src 已收紧）
#
# ⚠ 必须与 base.html 头部的 <meta http-equiv="Content-Security-Policy"> 保持一致：
# 浏览器对 meta 与响应头两份策略取交集，任何一边更严都会拦截另一边明确放行的资源。
# 曾因响应头缺 fonts.googleapis.com / fonts.gstatic.com / media-src blob:，
# 把页面自己声明并实际使用的标题字体样式表与 blob: 视频对比全部拦下
# （背景账本记于维护者本地文件 docs/project/KNOWN_ISSUES.md #54、陷阱 #7，未随仓库分发；
# 可执行的回归护栏是下面的默认值本身与 tests/ 里的 CSP 用例）。
# 标题字体切换器依赖的站酷小薇/马善政楷书等中文字体仅存在于 Google Fonts，
# 本地字体包（/static/fonts/）只有 DM Sans 与 Instrument Serif 两族，无法替代。
#
# S2 收紧说明（docs/CSP_TIGHTENING_ROADMAP.md，2026-09-06）：script-src 的实际
# 收紧在 meta 侧完成（nonce 上下文移除 unsafe-inline）；响应头侧无 per-request
# nonce 可用，保留 'unsafe-inline' 字符串不构成弱点——交集语义下 meta 的
# nonce-only 策略必然更严并生效。
# S4 Phase 2（WO-B17，2026-11-09）：应用侧内联 style 已清零（模板/UI JS/页内 <style>
# 块/Python 拼装均为 0，`.style.cssText` 属 CSSOM 不受 style-src 约束），style-src
# 两侧同步去掉 'unsafe-inline'，仅保留 'self' 与字体域（决策 D22 保留 Google Fonts）。
# 已知偏差：desktop/src/desktop-bridge.js 的更新弹窗仍用内联 style 拼 DOM（超出本工单
# 允许改动清单，见路线图 §2e），若启用该桌面壳，弹窗会掉样式。
_DEFAULT_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' https://fonts.googleapis.com; "
    "img-src 'self' data: blob:; "
    "media-src 'self' blob:; "
    "connect-src 'self'; "
    "font-src 'self' data: https://fonts.gstatic.com; "
    "object-src 'none'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)

# 与安全相关的固定响应头
_BASE_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """为所有 HTTP 响应注入安全响应头。

    Args:
        app: ASGI 应用。
        config: 可选的配置 dict（来自 ``load_config()``）；为空时按「已启用」处理。
            读取 ``config["security"]["headers"]`` 的 ``enabled`` 与 ``csp``。
    """

    def __init__(self, app, config: Any = None) -> None:
        super().__init__(app)
        self._config = config

    def _get_headers_config(self) -> dict[str, Any]:
        cfg = self._config
        if cfg is None:
            return {}
        # 兼容未来若改为 pydantic AppConfig 注入的场景
        if not isinstance(cfg, dict):
            sec = getattr(cfg, "security", None)
            hdr = getattr(sec, "headers", None) if sec is not None else None
            if hdr is None:
                return {}
            return hdr.model_dump() if hasattr(hdr, "model_dump") else dict(hdr)
        return (cfg.get("security", {}) or {}).get("headers", {}) or {}

    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)

        headers_cfg = self._get_headers_config()
        # 未配置时安全默认为"开启"
        enabled = True if not headers_cfg else bool(headers_cfg.get("enabled", True))
        if not enabled:
            return response

        for key, value in _BASE_HEADERS.items():
            response.headers.setdefault(key, value)

        csp = headers_cfg.get("csp", "") if headers_cfg else ""
        response.headers.setdefault("Content-Security-Policy", csp or _DEFAULT_CSP)

        return response


__all__ = ["SecurityHeadersMiddleware", "_DEFAULT_CSP"]
