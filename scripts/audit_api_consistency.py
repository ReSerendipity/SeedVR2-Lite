#!/usr/bin/env python3
"""audit_api_consistency.py — 前后端契约一致性审计（五个检查，一个入口）

为什么需要它
------------
「前端调用了后端不存在的东西」和「后端做了前端摸不到的东西」这两类不一致，
绝大多数**不会在控制台留下任何痕迹**，静态读代码也最难发现：

* 前端多调一个路径 → 404 / 405，只在特定交互下才暴露；
* 后端 ``Form(...)`` 未声明的字段 → FastAPI **静默丢弃**，用户调了参数毫无效果；
* 内联 ``onclick="foo()"`` 指向不存在的函数 → ReferenceError 一闪而过；
* 后端有路由、前端无入口 → 功能对用户完全不可见。

五个子命令
----------
``routes``          路由差集：A 类（前端调用 / 后端缺失）+ B 类（后端存在 / 前端无入口）
``docs``            网站 API 文档里写了、但后端不存在的路径（用户照文档调用即 404）
``form-fields``     前端提交的表单字段 vs 端点 OpenAPI requestBody 实际接受的字段
``inline-handlers`` 模板内联事件处理器引用的标识符是否都有定义
``orphans``         B 类逐条精确反查，并给出「有意为之 / 遗留死代码 / 真实缺口」判定
``all``             依次执行上述五项（默认）

路由清单来源（按优先级）
------------------------
1. ``--openapi <file.json>``
2. ``--base-url <url>``（GET ``<url>/openapi.json``，只读，不打扰正在跑的任务）
3. 进程内 ``create_app(load_config())`` —— 默认，无需先启动服务，约 20 秒

退出码
------
0 = 无新增未解释的不一致；1 = 有需要处理的新不一致（A 类命中，或出现未归档的孤儿路由）。
``--strict`` 会把「已知但仍未修」的死代码与缺口一并判失败。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
APP_DIR = REPO / "app" / "integrated_app"
TPL_DIR = APP_DIR / "templates"
JS_DIR = APP_DIR / "static" / "js"

# Vite 开发代理探针：由 routes/__init__.py 无条件挂载，不属于业务契约
_DEV_NOISE = re.compile(r"^/(?:@vite|@vite_ping|@react-refresh|__vite_ping|vite_ping|react-refresh|\.well-known/)")
# 页面路由（非 /api）也纳入差集，因为导航断链同样静默
_PAGE_METHODS = {"get"}

#: B 类孤儿路由归档表。``disposition``：
#:   intentional  = 有意为之（探针 / 服务端内部调用 / 对外集成面）
#:   api-surface  = 对外 API 面：tests / examples / 网站文档有消费，只是不接 Web UI
#:   no-consumer  = 全仓无任何消费者（UI、测试、文档、示例都没有）→ 待定夺
#:   dead-code    = 已知遗留，待清理
#:   gap          = 已知真实缺口，待补前端入口
#: 未出现在本表的新孤儿 = 未归档，审计判失败，强制维护者当场定性。
KNOWN_ORPHANS: dict[str, tuple[str, str]] = {
    "/metrics": ("intentional", "Prometheus 抓取端点，由监控侧调用，不经 UI"),
    "/api/system/ping": ("intentional", "容器 liveness 探针（Dockerfile HEALTHCHECK / K8s startupProbe）"),
    "/api/system/ready": ("intentional", "容器 readiness 探针，预热期返回 503+Retry-After"),
    "/api/system/shutdown": (
        "intentional",
        "本机运维端点（B-5 优雅关闭）：127.0.0.1 白名单 + CSRF 校验，仅桌面壳/本机运维调用，不经 Web UI",
    ),
    "/api/engine/detect": (
        "api-surface",
        "引擎抽象层对外集成面：仅 /docs Swagger 与外部客户端可达（tests/ 亦无消费者），Web UI 走 /api/restore",
    ),
    "/api/engine/list": ("api-surface", "同上"),
    "/api/engine/submit": ("api-surface", "同上"),
    "/api/engine/task/{}": ("api-surface", "同上"),
    "/api/system/model/load": (
        "api-surface",
        "tests/test_api.py 与 E2E api-mocks 消费；Web UI 不直连——提交任务时由服务端 ensure_model_loaded 自动加载",
    ),
    "/api/system/model/unload": (
        "api-surface",
        "tests/test_api.py 与 E2E api-mocks 消费；空闲超时卸载由后端 lifespan 任务驱动",
    ),
    "/api/system/model/switch": (
        "api-surface",
        "tests/test_api.py 与 E2E api-client 消费；UI 侧改尺寸/精度由 ensure_model_loaded 对齐 dit_model 时代替",
    ),
    "/api/system/history/resolve": (
        "api-surface",
        "tests/test_output_provenance.py 消费的内部路径解析，供下载/缩略图链路使用",
    ),
    "/api/system/history/{}/pin": (
        "api-surface",
        "tests/test_pinned_retention.py 专测 + test_api.py 消费的 retention 豁免端点"
        "（pinned 标记/取消，历史 schema v3）；UI 历史页暂未接按钮，走 API/外部客户端",
    ),
    "/api/system/history/recycle": (
        "api-surface",
        "tests/test_api.py::TestHistoryRecycleAPI 消费的回收站列表（历史 schema v4 软删除）；"
        "UI 历史页暂未接回收站入口，走 API/外部客户端",
    ),
    "/api/system/history/recycle/restore": (
        "api-surface",
        "tests/test_api.py::TestHistoryRecycleAPI 消费的回收站恢复端点（同 v4 软删除特性）",
    ),
    "/api/system/history/recycle/purge": (
        "api-surface",
        "tests/test_api.py::TestHistoryRecycleAPI 消费的回收站超期彻底清理端点（同 v4 软删除特性）",
    ),
    "/api/system/gpu/system": (
        "api-surface",
        "tests/test_api.py 与 E2E api-mocks 消费的系统级 GPU 汇总，UI 走 /api/system/gpu",
    ),
    "/api/system/gpu/vram-estimate": (
        "api-surface",
        "examples/ 与 website/docs/guide/{api,vram}.md 收录的显存估算接口",
    ),
    "/api/system/locales": (
        "api-surface",
        "tests/test_api.py 消费的可翻译语言清单；UI 用 POST /api/system/locale 直接切换",
    ),
    "/api/system/metrics": ("api-surface", "tests + locustfile 消费的指标快照（与 Prometheus /metrics 是两套口径）"),
    "/api/system/metrics/inference": ("api-surface", "tests/test_api.py 消费的推理历史列表"),
    "/api/ui/layout": ("api-surface", "tests/test_ui_routes.py 消费"),
    "/api/ui/parameters": ("api-surface", "tests/test_ui_routes.py 消费"),
    "/api/ui/parameters/recommendations": ("api-surface", "tests/test_ui_routes.py 消费"),
    "/api/ui/parameters/validate": ("api-surface", "tests/test_ui_routes.py / test_property_based.py 消费"),
    "/api/ui/preferences/reset": ("api-surface", "tests/test_ui_routes.py 消费；UI 侧重置走前端本地清 sv_* 键"),
    "/api/system/metrics/reset": (
        "intentional",
        "运维端点（经 /docs 可达）：它是 MetricsCollector.reset() 的**唯一**调用者，"
        "删端点只会把该方法变成孤儿——用一种死码换另一种，故保留。曾误标 no-consumer",
    ),
    "/api/restore/{}/result": (
        "api-surface",
        "examples/api_example.js:324 与 examples/api_example.py:305 实际在调用（examples/ 为维护者本地目录，未随仓库分发）；"
        "曾误标 dead-code（漏看了 examples 消费者），本批补进 website/docs/guide/api.md",
    ),
    "/api/downloads/catalog": (
        "intentional",
        "对外集成面：桌面壳/外部客户端读取可下载清单（环境包分卷与校验和 + 权重变体）；"
        "仓内下载页只用 status/progress/start/cancel/uninstall，无 UI 入口。"
        "2026-11-05 补登记（此前未归档导致契约审计红）",
    ),
}

_HTTP_METHODS = ("get", "post", "put", "delete", "patch")
_JS_METHOD_RE = re.compile(r"""\bapi\.(get|post|put|delete|patch)\s*\(""", re.I)
_HX_METHOD_RE = re.compile(r"""hx-(get|post|put|delete|patch)\s*=\s*["']""", re.I)
_FETCH_METHOD_RE = re.compile(r"""\bmethod\s*:\s*['"]([A-Za-z]+)['"]""")


# --------------------------------------------------------------------------------------
# 公共：源码与路径规范化
# --------------------------------------------------------------------------------------


def frontend_sources() -> list[tuple[Path, str]]:
    """全部前端源码（模板 + 自建 JS）。不含 node_modules 与第三方库。"""
    files = sorted(TPL_DIR.rglob("*.html")) + sorted(JS_DIR.rglob("*.js"))
    return [(f, f.read_text(encoding="utf-8")) for f in files if f.is_file()]


def short_name(path: Path) -> str:
    text = str(path).replace("\\", "/")
    return text.split("integrated_app/")[-1]


_PARAM_SEG = re.compile(r"\{[^}]*\}")


def norm_backend(path: str) -> str:
    """``/api/restore/{task_id:path}/x`` → ``/api/restore/{}/x``（参数段统一成 ``{}``）。"""
    path = _PARAM_SEG.sub("{}", path)
    return path.rstrip("/") or "/"


def norm_frontend(path: str) -> str:
    """前端字面量归一：模板插值 ``${x}`` / Jinja ``{{ x }}`` → ``{}``，去查询串与尾斜杠。"""
    path = path.split("?", 1)[0].split("#", 1)[0]
    path = re.sub(r"\$\{[^{}]*\}", "{}", path)
    path = re.sub(r"\{\{[^{}]*\}\}", "{}", path)
    path = re.sub(r"%7B[^%]*%7D", "{}", path)
    path = _PARAM_SEG.sub("{}", path)
    return path.rstrip("/") or "/"


def seg_match(front: str, back: str) -> bool:
    """按段比较，**通配只允许后端方向**。

    只有后端参数段（``{record_id}`` → ``{}``）可以吸收前端任意段；前端的
    ``{}``（来自 ``${id}`` 这类动态拼接）**不得**匹配后端字面段——否则前端
    ``/api/system/history/${id}`` 会「顺便覆盖」后端字面路由
    ``/api/system/history/table``，把真正的孤儿端点漏判成已有入口。
    """
    a, b = front.split("/"), back.split("/")
    if len(a) != len(b):
        return False
    return all(bx == "{}" or fx == bx for fx, bx in zip(a, b, strict=True))


# --------------------------------------------------------------------------------------
# 前端：URL 抽取
# --------------------------------------------------------------------------------------

# 字符串字面量（三种引号），内容需以 / 开头才可能是站内路径
_LITERAL = re.compile(r"""(?P<q>['"`])(?P<body>(?:\\.|(?!(?P=q)).)*)(?P=q)""")
# 属性型 URL
_ATTR = re.compile(r"""\b(?:href|src|action)\s*=\s*["'](/[^"'#\s]*)""")
# 表单控件的 name（限定标签，避免把 <meta name=...> 当参数）
_FORM_CTRL = re.compile(r"""<(?:input|select|textarea)\b[^>]*?\bname=["']([^"']+)["']""", re.S | re.I)
_APPEND = re.compile(r"""\b(?:params|formData|body|payload|data|query)\s*\.\s*append\(\s*['"]([^'"]+)['"]""")
# 形状闸门：每段只允许 单词字符/点/连字符 或 {}，挡住拼接误伤（'a(' + n + '/' + m + ')' → '/{})'）
_ROUTE_SHAPE = re.compile(r"^/(?:[\w.\-]+|\{\})(?:/(?:[\w.\-]+|\{\}))*$")


def _expand_concat(line: str, start: int) -> tuple[str, int]:
    """把 ``'/api/restore/' + savedTaskId + '/cancel'`` 展开成 ``/api/restore/{}/cancel``。

    从 ``start``（一个字符串字面量的开头）出发，向右贪心吃 ``+ 表达式 + 字面量`` 链。
    非字面量片段一律记作 ``{}``——它必然是运行期变量。
    """
    m = _LITERAL.match(line, start)
    if not m:
        return "", start
    out = m.group("body")
    pos = m.end()
    while True:
        rest = re.match(r"""\s*\+\s*""", line[pos:])
        if not rest:
            break
        after = pos + rest.end()
        nxt = _LITERAL.match(line, after)
        if nxt:
            out += nxt.group("body")
            pos = nxt.end()
            continue
        ident = re.match(r"""[^+\s]+(?:\s*\?\s*[^:+]+:[^:+]+)?""", line[after:])
        if not ident:
            break
        out += "{}"
        pos = after + ident.end()
    return out, pos


def extract_urls(src: str) -> list[tuple[str, str | None]]:
    """返回 ``[(归一化路径, HTTP 方法或 None)]``。

    逐行扫描字符串字面量（三种引号），内容以 ``/`` 开头才视为站内路径；
    遇到 ``lit + var + lit`` 拼接链会整体展开（见 :func:`_expand_concat`）。
    """
    found: list[tuple[str, str | None]] = []
    for line in src.splitlines():
        pos = 0
        while True:
            m = _LITERAL.search(line, pos)
            if not m:
                break
            body = m.group("body")
            if body.startswith("/") and not body.startswith("//"):
                raw, end = _expand_concat(line, m.start())
                path = norm_frontend(raw)
                if path == "/" or _ROUTE_SHAPE.match(path):
                    found.append((path, _method_of(line, m.start())))
                pos = end
                continue
            pos = m.end()
        for am in _ATTR.finditer(line):
            attr_path = norm_frontend(am.group(1))
            if attr_path == "/" or _ROUTE_SHAPE.match(attr_path):
                found.append((attr_path, None))
    return found


def _method_of(line: str, pos: int) -> str | None:
    """从字面量左侧的调用上下文推断 HTTP 方法。推断不出就返回 None（差集按路径比对）。"""
    left = line[:pos]
    hm = _JS_METHOD_RE.search(left)
    if hm:
        return hm.group(1).upper()
    hx = _HX_METHOD_RE.search(left)
    if hx:
        return hx.group(1).upper()
    if re.search(r"\bfetch\s*\(", left):
        fm = _FETCH_METHOD_RE.search(line)
        return fm.group(1).upper() if fm else None
    if re.search(r"new\s+EventSource\s*\(|^\s*(?:import|<link|<script)", line):
        return "GET"
    return None


# --------------------------------------------------------------------------------------
# 后端：路由清单
# --------------------------------------------------------------------------------------


def load_openapi(openapi_file: str | None, base_url: str | None) -> dict[str, Any]:
    if openapi_file:
        return json.loads(Path(openapi_file).read_text(encoding="utf-8"))
    if base_url:
        url = base_url.rstrip("/") + "/openapi.json"
        with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310 - 固定本机回环地址
            return json.loads(resp.read().decode("utf-8"))
    sys.path.insert(0, str(REPO))
    from app.integrated_app.app_server import create_app  # noqa: PLC0415 - 进程内引导，重依赖延迟导入
    from app.integrated_app.config import load_config  # noqa: PLC0415

    config = load_config()
    config.setdefault("model", {})["auto_load"] = False
    config.setdefault("server", {})["auto_open_browser"] = False
    return create_app(config).openapi()


def backend_routes(spec: dict[str, Any]) -> dict[str, set[str]]:
    """``{归一化路径: {允许的方法}}``。"""
    out: dict[str, set[str]] = {}
    for raw, ops in spec.get("paths", {}).items():
        if _DEV_NOISE.match(raw):
            continue
        path = norm_backend(raw)
        methods = {str(m).upper() for m in ops if str(m).lower() in _HTTP_METHODS}
        out.setdefault(path, set()).update(methods or _PAGE_METHODS)
    return out


def _deref(spec: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    ref = schema.get("$ref")
    if not ref:
        return schema
    node: Any = spec
    for part in ref.lstrip("#/").split("/"):
        node = node.get(part, {}) if isinstance(node, dict) else {}
    return node if isinstance(node, dict) else {}


def endpoint_form_fields(spec: dict[str, Any]) -> dict[str, set[str]]:
    """每个 ``POST`` 端点实际接受的 form 字段集合（来自 OpenAPI requestBody）。"""
    result: dict[str, set[str]] = {}
    for raw, ops in spec.get("paths", {}).items():
        post = ops.get("post") or {}
        content = (post.get("requestBody") or {}).get("content") or {}
        for ctype in ("application/x-www-form-urlencoded", "multipart/form-data"):
            block = content.get(ctype)
            if not block:
                continue
            schema = _deref(spec, block.get("schema") or {})
            fields = set((schema.get("properties") or {}).keys())
            fields |= set(schema.get("required") or [])
            if fields:
                result[f"{raw.rstrip('/') or '/'}"] = fields
    return result


# --------------------------------------------------------------------------------------
# 检查 1：路由差集
# --------------------------------------------------------------------------------------


def check_routes(spec: dict[str, Any]) -> dict[str, list[Any]]:
    routes = backend_routes(spec)
    refs: dict[str, list[tuple[str, str]]] = {}
    for f, src in frontend_sources():
        for path, method in extract_urls(src):
            refs.setdefault(path, []).append((short_name(f), method or "-"))

    def hits_back(front_path: str) -> list[str]:
        return [bp for bp in routes if seg_match(front_path, bp)]

    a_missing, a_method, slash_only = [], [], []
    for front_path, places in sorted(refs.items()):
        if not front_path.startswith("/"):
            continue
        if front_path.startswith("/static/"):
            continue  # 由 Mount 提供，单独走磁盘存在性校验
        matched = hits_back(front_path)
        if matched:
            used = {m for _, m in places if m != "-"}
            allowed = set().union(*(routes[p] for p in matched)) if matched else set()
            if used and allowed and not (used & allowed):
                a_method.append((front_path, sorted(used), sorted(allowed), places[:2]))
            continue
        # FastAPI redirect_slashes 会补尾斜杠：只差一个斜杠不算缺失，单独降级提示
        relaxed = [bp for bp in routes if seg_match(front_path + "/", bp) or seg_match(front_path, bp + "/")]
        if relaxed:
            slash_only.append((front_path, relaxed[0], places[:2]))
        else:
            a_missing.append((front_path, places))

    static_missing = []
    for front_path, places in sorted(refs.items()):
        if not front_path.startswith("/static/"):
            continue
        on_disk = APP_DIR / front_path.lstrip("/")
        if not on_disk.exists():
            static_missing.append((front_path, places[:2]))

    b_orphans = []
    for bp in sorted(routes):
        if not any(seg_match(fp, bp) for fp in refs):
            b_orphans.append((bp, sorted(routes[bp])))
    return {
        "a_missing": a_missing,
        "a_method": a_method,
        "a_slash": slash_only,
        "static_missing": static_missing,
        "b_orphans": b_orphans,
        "frontend_total": len(refs),
        "backend_total": len(routes),
    }


# --------------------------------------------------------------------------------------
# 检查 1b：网站 API 文档 vs 后端路由
# --------------------------------------------------------------------------------------

DOC_API = REPO / "website" / "docs" / "guide" / "api.md"
_DOC_ROW = re.compile(r"^\|\s*(GET|POST|PUT|DELETE|PATCH)\s*\|\s*`([^`]+)`\s*\|", re.I)


def check_docs(spec: dict[str, Any]) -> dict[str, Any]:
    """文档里写了但后端不存在的路径。

    这类不一致的后果最直接：用户照着网站文档调用，拿到 404。实测本仓
    ``api.md`` 曾列有 ``/api/system/sse``、``/api/restore/task/{task_id}``、
    ``/api/tasks/queue`` 等**根本不存在**的端点。
    """
    if not DOC_API.is_file():
        return {"missing": [], "documented": 0, "exists": False}
    routes = backend_routes(spec)
    missing: list[tuple[str, str]] = []
    total = 0
    for line in DOC_API.read_text(encoding="utf-8").splitlines():
        m = _DOC_ROW.match(line.strip())
        if not m:
            continue
        total += 1
        method, path = m.group(1).upper(), norm_frontend(m.group(2))
        matched = next((bp for bp in routes if seg_match(path, bp)), None)
        if matched is None or method not in routes[matched]:
            missing.append((f"{method} {path}", matched or ""))
    return {"missing": missing, "documented": total, "exists": True}


# --------------------------------------------------------------------------------------
# 检查 2：表单字段
# --------------------------------------------------------------------------------------


def check_form_fields(spec: dict[str, Any]) -> dict[str, Any]:
    front: set[str] = set()
    for _f, src in frontend_sources():
        for m in _FORM_CTRL.finditer(src):
            front.add(m.group(1).strip())
        for m in _APPEND.finditer(src):
            front.add(m.group(1).strip())
    front -= {"file", "files", "folder_path", "csrf_token"}
    front = {f for f in front if f}

    accepted: set[str] = set()
    endpoints = endpoint_form_fields(spec)
    for fields in endpoints.values():
        accepted |= fields
    return {
        "dropped": sorted(front - accepted),
        "backend_only": sorted(accepted - front),
        "front_total": len(front),
        "accepted_total": len(accepted),
        "endpoints": {k: len(v) for k, v in sorted(endpoints.items())},
    }


# --------------------------------------------------------------------------------------
# 检查 3：内联事件处理器
# --------------------------------------------------------------------------------------

_DEF_PATTERNS = [
    re.compile(r"\bfunction\s+([A-Za-z_$][\w$]*)\s*\("),
    re.compile(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*="),
    re.compile(r"\bwindow\.([A-Za-z_$][\w$]*)\s*="),
    re.compile(r"^\s*(?:async\s+)?([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{", re.M),  # 对象/类简写方法
    re.compile(r"\b([A-Za-z_$][\w$]*)\s*:\s*(?:async\s*)?(?:function\b|\([^)]*\)\s*=>|[A-Za-z_$][\w$.]*\s*=>)"),
    re.compile(r"\b([A-Za-z_$][\w$]*)\s*:\s*(?:async\s+)?function\s*\("),
]
_CTRL_KEYWORDS = {
    "if",
    "for",
    "while",
    "switch",
    "catch",
    "return",
    "function",
    "constructor",
    "get",
    "set",
    "typeof",
    "await",
    "do",
    "else",
    "try",
    "new",
    "delete",
    "in",
    "of",
    "case",
    "default",
}
_INLINE_HANDLER = re.compile(
    r"""\bon(?:click|change|submit|input|load|error|keydown|keyup|keypress|focus|blur|
        mouseover|mouseout|mousedown|mouseup|contextmenu|dblclick|toggle)\s*=\s*["']([^"']*)["']""",
    re.X | re.I,
)
_GLOBALS = {
    "alert",
    "confirm",
    "prompt",
    "print",
    "fetch",
    "event",
    "location",
    "history",
    "scrollBy",
    "scrollTo",
    "setTimeout",
    "clearTimeout",
    "setInterval",
    "clearInterval",
    "requestAnimationFrame",
    "import",
    "require",
    "JSON",
    "Math",
    "Date",
    "Object",
    "Array",
    "String",
    "Number",
    "Boolean",
    "Promise",
    "Intl",
    "encodeURIComponent",
    "decodeURIComponent",
    "isNaN",
    "parseInt",
    "parseFloat",
    "htmx",
    "open",
    "close",
    "stopPropagation",
    "preventDefault",
    "document",
    "window",
    "this",
    "true",
    "false",
    "null",
    "undefined",
}


def check_inline_handlers() -> list[str]:
    files = frontend_sources()
    defined: set[str] = set(_GLOBALS)
    for _, src in files:
        for pat in _DEF_PATTERNS:
            defined.update(n for n in pat.findall(src) if n not in _CTRL_KEYWORDS)
        # 导出对象字面量里的键（SeedVR2 = { foo, bar: fn }）与解构导入
        defined.update(re.findall(r"[{,]\s*([A-Za-z_$][\w$]*)\s*[:,}]", src))

    problems: list[str] = []
    for f, src in files:
        if f.suffix != ".html":
            continue
        for lineno, line in enumerate(src.splitlines(), 1):
            for hm in _INLINE_HANDLER.finditer(line):
                called = re.findall(r"(?<![.\w$])([A-Za-z_$][\w$]*)\s*\(", hm.group(1))
                missing = sorted({name for name in called if name not in defined})
                if missing:
                    problems.append(f"{short_name(f)}:{lineno} 引用未定义 {missing} → {line.strip()[:120]}")
    return problems


# --------------------------------------------------------------------------------------
# 检查 4：孤儿路由反查
# --------------------------------------------------------------------------------------


def _probe_pattern(path: str) -> re.Pattern[str]:
    """整条静态路径做探针，参数段 ``{}`` 编译成 ``.{0,40}``。

    必须带前后边界断言：只用「尾段子串」会大面积假命中——``/system`` 命中
    ``/api/system/settings``、``/load`` 命中注释里的 ``loading``、``/metrics``
    命中 ``/api/system/metrics``，展示出来的「前端引用」全是假的。
    ``.{0,40}`` 同时覆盖 ``${id}`` 模板插值与 ``'/a/' + id + '/b'`` 字符串拼接。
    """
    body = r".{0,40}".join(re.escape(p) for p in path.split("{}"))
    return re.compile(rf"(?<![\w/.{{}}-]){body}(?![\w/-])")


def classify_orphans(b_orphans: list[tuple[str, set[str]]]) -> list[dict[str, Any]]:
    files = frontend_sources()
    rows: list[dict[str, Any]] = []
    for path, methods in b_orphans:
        pattern = _probe_pattern(path)
        hits: list[str] = []
        for f, src in files:
            for lineno, line in enumerate(src.splitlines(), 1):
                if pattern.search(line):
                    hits.append(f"{short_name(f)}:{lineno}")
                    break
        disposition, reason = KNOWN_ORPHANS.get(path, ("unclassified", "未归档——需要维护者定性"))
        rows.append(
            {
                "path": path,
                "methods": "/".join(sorted(methods)),
                "probe_hits": sorted(set(hits))[:3],
                "disposition": disposition,
                "reason": reason,
            }
        )
    return rows


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def _print_routes(res: dict[str, Any], strict: bool) -> int:
    print(f"[后端路由] {res['backend_total']} 条 · [前端引用路径] {res['frontend_total']} 条")
    rc = 0

    print("\n=== A 类 · 前端调用但后端不存在 ===")
    if not res["a_missing"]:
        print("  无（0 处）")
    for path, places in res["a_missing"]:
        print(f"  ✗ {path}  ← {', '.join(f'{f}:{m}' for f, m in places)}")
        rc = 1

    if res["a_method"]:
        print("\n=== A 类 · 路径存在但方法不匹配（会拿到 405）===")
        for path, used, allowed, places in res["a_method"]:
            print(f"  ! {path}  前端 {used} vs 后端 {allowed}  ← {places}")
            rc = 1

    if res["a_slash"]:
        print("\n=== 提示 · 仅差尾斜杠（redirect_slashes 会 307 兜住，非缺陷，但多一次往返）===")
        for path, bp, places in res["a_slash"]:
            print(f"  ~ {path} → {bp}/  ← {', '.join(f for f, _ in places)}")

    if res["static_missing"]:
        print("\n=== 提示 · 前端引用的 /static/ 资源在磁盘上不存在（静默 404）===")
        for path, places in res["static_missing"]:
            print(f"  ✗ {path}  ← {', '.join(f for f, _ in places)}")
            rc = 1

    print("\n=== B 类 · 后端存在但前端无入口（详见 orphans 子命令）===")
    if not res["b_orphans"]:
        print("  无")
    for row in classify_orphans(res["b_orphans"]):
        tag = {
            "intentional": "·",
            "api-surface": "a",
            "no-consumer": "n",
            "dead-code": "†",
            "gap": "✗",
            "unclassified": "?",
        }[row["disposition"]]
        print(f"  {tag} {row['methods']:<11} {row['path']}  [{row['disposition']}] {row['reason']}")
        if row["disposition"] == "unclassified" or (
            strict and row["disposition"] in {"gap", "dead-code", "no-consumer"}
        ):
            rc = 1
    return rc


def _print_orphans(spec: dict[str, Any]) -> int:
    res = check_routes(spec)
    rows = classify_orphans(res["b_orphans"])
    print(f"{'判定':<6}{'路由':<46}{'前端探针命中'}")
    print("-" * 110)
    for row in rows:
        mark = {
            "intentional": "有意",
            "api-surface": "API面",
            "no-consumer": "无消费",
            "dead-code": "死码",
            "gap": "缺口",
            "unclassified": "未归档",
        }[row["disposition"]]
        print(f"{mark:<6}{row['methods'] + ' ' + row['path']:<46}{', '.join(row['probe_hits']) or '—'}")
        print(f"{'':6}  {row['reason']}")
    unknown = [r for r in rows if r["disposition"] == "unclassified"]
    print(f"\n合计 {len(rows)} 条；未归档 {len(unknown)} 条")
    return 1 if unknown else 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="前后端契约一致性审计")
    parser.add_argument(
        "command",
        nargs="?",
        default="all",
        choices=["routes", "docs", "form-fields", "inline-handlers", "orphans", "all"],
    )
    parser.add_argument("--openapi", help="从 JSON 文件读路由清单")
    parser.add_argument("--base-url", help="从运行中的实例 GET /openapi.json（只读）")
    parser.add_argument("--json", action="store_true", help="机器可读输出")
    parser.add_argument("--strict", action="store_true", help="已知死代码/缺口也判失败")
    args = parser.parse_args(argv)

    needs_spec = args.command in {"routes", "docs", "form-fields", "orphans", "all"}
    spec = load_openapi(args.openapi, args.base_url) if needs_spec else {}

    rc = 0
    if args.command in {"routes", "all"}:
        res = check_routes(spec)
        if args.json:
            print(json.dumps(res, ensure_ascii=False, default=str, indent=2))
        else:
            print("\n" + "=" * 96 + "\n检查 1/5 · 路由差集\n" + "=" * 96)
            rc |= _print_routes(res, args.strict)
    if args.command in {"docs", "all"}:
        doc = check_docs(spec)
        if args.json:
            print(json.dumps(doc, ensure_ascii=False, indent=2))
        else:
            print("\n" + "=" * 96 + "\n检查 2/5 · 网站 API 文档 vs 后端路由\n" + "=" * 96)
            if not doc["exists"]:
                print("  · 未找到 website/docs/guide/api.md，跳过")
            elif doc["missing"]:
                print(f"  ✗ 文档收录 {doc['documented']} 条，其中 {len(doc['missing'])} 条后端不存在或方法不符：")
                for documented, nearest in doc["missing"]:
                    print(f"      {documented}" + (f"   （最接近：{nearest}）" if nearest else ""))
                rc = 1
            else:
                print(f"  ✓ 文档收录 {doc['documented']} 条端点全部真实存在且方法匹配")
    if args.command in {"form-fields", "all"}:
        ff = check_form_fields(spec)
        if args.json:
            print(json.dumps(ff, ensure_ascii=False, indent=2))
        else:
            print("\n" + "=" * 96 + "\n检查 3/5 · 表单字段前后端对齐\n" + "=" * 96)
            print(f"前端提交 {ff['front_total']} 个字段；后端接收面 {ff['accepted_total']} 个；端点 {ff['endpoints']}")
            if ff["dropped"]:
                print("  ✗ 前端提交但后端不接收（FastAPI 静默丢弃，用户调了没效果）：")
                print("\n".join(f"      {f}" for f in ff["dropped"]))
                rc = 1
            else:
                print("  ✓ 无静默丢弃字段")
            if ff["backend_only"]:
                print(f"  · 后端接收但前端不提交（走默认值，共 {len(ff['backend_only'])} 个）：{ff['backend_only']}")
    if args.command in {"inline-handlers", "all"}:
        problems = check_inline_handlers()
        if args.json:
            print(json.dumps(problems, ensure_ascii=False, indent=2))
        else:
            print("\n" + "=" * 96 + "\n检查 4/5 · 内联事件处理器是否绑定真实函数\n" + "=" * 96)
            if problems:
                print("\n".join(f"  ✗ {p}" for p in problems))
                rc = 1
            else:
                print("  ✓ 全部有定义")
    if args.command == "orphans":
        rc |= _print_orphans(spec)

    print("\n" + ("✗ 存在需要处理的不一致" if rc else "✓ 前后端契约一致") + f"（退出码 {rc}）")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
