"""i18n 取词完整性回归测试（2026-08-31 UI 精修 Stage A 引入）。

覆盖三类此前无任何门禁、只能靠肉眼截图发现的缺陷：

1. **模板裸键泄漏**：`t()` 未命中时返回键本身（真值），因此模板里
   `{{ t('x') or '中文' }}` 的回退永不生效，缺键会直接把 `restore.output_format`
   这类字符串渲染到页面上。
2. **前端取词漏登记**：base.html 曾以手写白名单向 `window.__I18N__` 暴露翻译键，
   JS 实际引用的键若忘了登记就静默退化成硬编码中文。现改为整表导出，
   本测试守住「JS 引用的每个键都能在注入表里取到」。
3. **版本号漂移**：模板里硬编码 v1.0.0 会与 pyproject 脱钩，
   本测试守住页面版本串跟随 get_app_version()。

运行：pytest tests/test_i18n_completeness.py -q
"""

import json
import re
from pathlib import Path

import pytest

LOCALES_DIR = Path(__file__).resolve().parent.parent / "app/integrated_app/locales"
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "app/integrated_app/templates"
LANGS = ["zh", "zh-TW", "en", "ja", "fr"]

PAGES = ["/", "/restore", "/history", "/system-status", "/settings"]

# 模板里 t('namespace.key') 形式的调用
T_CALL = re.compile(r"""\bt\(\s*['"]([A-Za-z0-9_.\-]+)['"]""")
# JS 里 I['namespace.key'] / I["key"] 形式的取词。
# 负向预查排除 I['prefix.' + expr] 这类动态拼接取词：字面量前缀无法静态校验，
# 由运行时的 || 兜底保证不崩。不排掉就会把 'status.' 当成键误报。
JS_LOOKUP = re.compile(r"""\bI\[\s*['"]([A-Za-z0-9_.\-]+)['"]""")


def _static_lookup_keys(source: str) -> set[str]:
    r"""只保留静态字面量取词，剔除动态拼接的字面量前缀。

    I['status.' + r.status] 这类写法会被正则抓出 'status.'，而合法的命名空间键
    永远不会以 '.' 结尾，据此判定为动态取词并跳过（由运行时的 || 兜底保证不崩）。
    注意别改用 \s*(?!\+) 排除：\s* 可回溯，匹配零个空格即可绕过 lookahead。"""
    return {k for k in JS_LOOKUP.findall(source) if not k.endswith(".")}


# 渲染结果中的裸键（命名空间.蛇形名），用于扫描可见文本
BARE_KEY = re.compile(r"\b[a-z]{3,}(?:_[a-z0-9]+){0,2}\.[a-z][a-z0-9_]{2,}\b")
# 可见文本里合法出现的花括号点号串（文件名/域名等），按需白名单
TEXT_ALLOWLIST = {
    "config.yaml",
    "app.log",
    "index.html",
    "style.css",
    "start.bat",
    "install.bat",  # 关于页快速开始正当提及安装脚本文件名，非 i18n 泄漏
    "package.json",
    "python.exe",
    "requirements.txt",
    "history.db",
    "model_lib",
    # torch.compile 是 PyTorch 的 API 名，作为技术术语出现在编译开关的标签与提示里，
    # 五种语言都不译（同 python.exe / model_lib 的先例），不是漏翻译。
    "torch.compile",
}


def _load_all() -> dict[str, dict]:
    return {lang: json.loads((LOCALES_DIR / f"{lang}.json").read_text(encoding="utf-8")) for lang in LANGS}


def _resolves(data: dict, key: str) -> bool:
    """按 i18n._resolve_key 的语义判断键是否可解析为字符串。"""
    if key in data and isinstance(data[key], str):
        return True
    node = data
    for part in key.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return False
    return isinstance(node, str)


@pytest.mark.parametrize("lang", LANGS)
def test_template_keys_resolve_in_every_locale(lang: str) -> None:
    """模板引用的每个翻译键，都必须在全部 5 个语言文件里可解析。

    缺任何一个语言，该语言用户就会看到裸键（t() 未命中返回键名，
    而 `or '中文'` 回退因键名为真值而永不触发）。
    """
    data = _load_all()[lang]
    missing: list[str] = []
    for tpl in sorted(TEMPLATES_DIR.glob("*.html")):
        for key in T_CALL.findall(tpl.read_text(encoding="utf-8")):
            if not _resolves(data, key) and key not in missing:
                missing.append(key)
    assert not missing, f"{lang}.json 缺少模板用到的键: {sorted(missing)}"


def test_no_dead_fallback_copy_idioms() -> None:
    """禁止两类永不生效的硬编码兜底写法（评估 R1/E20 整改，2026-11-06 收口）。

    t() 未命中返回键本身（非空字符串，真值），因此两种写法的兜底分支都取不到，
    只会掩盖缺键、把硬编码文案留在源码里：
    1. `{{ t('x') or '中文' }}`——or 分支永不触发；
    2. `{{ t('x') if t('x') else '文案' }}`——else 分支永不触发
      （index.html「No restoration tasks yet.」等 7 处即此型，历史测试只禁 or 型漏网）。
    需要兜底请用 `t('x', default='…')`，或者把键补进 5 个词表。
    """
    offenders: list[str] = []
    patterns = [
        # 型 1：t('x') or '…'
        re.compile(r"""\{\{\s*t\(\s*['"][^'"]+['"]\s*\)\s+or\s+"""),
        # 型 2：t('x') if t('x') else '…'（同一键重复出现的条件表达式死回退）
        re.compile(r"""\{\{\s*t\(\s*['"]([^'"]+)['"]\s*\)\s+if\s+t\(\s*['"]([^'"]+)['"]\s*\)\s+else\s+"""),
    ]
    for tpl in sorted(TEMPLATES_DIR.glob("*.html")):
        for lineno, line in enumerate(tpl.read_text(encoding="utf-8").splitlines(), 1):
            for pattern in patterns:
                if pattern.search(line):
                    offenders.append(f"{tpl.name}:{lineno}")
                    break
    assert not offenders, f"失效的兜底写法（应改为补键或 t(k, default=…)）: {offenders}"


# 语言选择器的本族语名（i18n 惯例：语言名用它自己的语言展示，不进词表）
_NATIVE_LANGUAGE_NAMES = {"中文", "繁體中文", "日本語", "简中", "繁中"}

# CJK 字符（汉字 + CJK 标点 + 全角形式）：出现在剔除注释/脚本/样式后的
# 可见模板内容里即为硬编码文案残留
_CJK = re.compile(r"[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]+")

# 剥离顺序敏感：先 Jinja 注释/表达式（内嵌 HTML 注释会被整体吞掉），再 HTML 注释，
# 再 script/style 块（JS/CSS 的中文注释与字符串是开发面向，非页面文案），
# 最后剥标签留文本节点+属性值。剥离用「同位空格化」——保留换行结构，
# 使命中位置的行号与原文件一一对应。
_JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.S)
_JINJA_TAG = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.S)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_SCRIPT_OR_STYLE = re.compile(r"<script\b.*?</script>|<style\b.*?</style>", re.S)
_ANY_TAG = re.compile(r"<[^>]+>")


def _blank_keep_newlines(pattern: re.Pattern[str], text: str) -> str:
    """把 pattern 命中段替换为等长空白（换行保留），维持行号映射。"""
    return pattern.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)


def test_no_cjk_in_visible_template_text() -> None:
    """模板可见文本（文本节点+属性值）不得含硬编码 CJK 文案（评估 R1/E20 整改）。

    扫描口径：剔除 Jinja 注释/表达式、HTML 注释、script/style 块后逐模板扫描
    剩余内容（文本节点与属性值）。语言选择器的本族语名（中文/繁體中文/日本語）
    按 i18n 惯例白名单豁免；真正的页面文案必须走 t() 键。
    """
    offenders: list[str] = []
    for tpl in sorted(TEMPLATES_DIR.glob("*.html")):
        source = tpl.read_text(encoding="utf-8")
        stripped = source
        for pattern in (_JINJA_COMMENT, _JINJA_TAG, _HTML_COMMENT, _SCRIPT_OR_STYLE, _ANY_TAG):
            stripped = _blank_keep_newlines(pattern, stripped)
        for match in _CJK.finditer(stripped):
            text = match.group(0).strip()
            if text in _NATIVE_LANGUAGE_NAMES:
                continue
            lineno = stripped.count("\n", 0, match.start()) + 1
            offenders.append(f"{tpl.name}:{lineno}: {text!r}")
    assert not offenders, "模板可见文本含硬编码 CJK 文案（应改走 t() 键）:\n" + "\n".join(offenders)


def test_js_referenced_keys_are_injected(test_app) -> None:
    """JS 里 I['…'] 取用的每个键，都必须出现在整表注入的 __I18N__ 中。

    这是旧手写白名单最容易出问题的地方：漏登记不会报错，只会静默显示中文。
    """
    client = test_app
    html = client.get("/restore").text
    start = html.find("window.__I18N__ = ")
    assert start != -1, "页面未注入 window.__I18N__"

    injected = set(re.findall(r'"([A-Za-z0-9_.\-]+)":', html[start : start + 200_000]))

    used: set[str] = set()
    for src in list(TEMPLATES_DIR.glob("*.html")) + list((TEMPLATES_DIR.parent / "static/js").glob("*.js")):
        used |= _static_lookup_keys(src.read_text(encoding="utf-8"))

    missing = sorted(k for k in used if k not in injected)
    assert not missing, f"JS 取词但 __I18N__ 未提供的键: {missing}"


@pytest.mark.parametrize("page", PAGES)
def test_rendered_pages_have_no_bare_key_in_visible_text(test_app, page: str) -> None:
    """页面可见文本里不得出现裸翻译键。"""
    resp = test_app.get(page)
    assert resp.status_code == 200, f"{page} -> HTTP {resp.status_code}"

    body = re.sub(r"<script.*?</script>|<style.*?</style>", " ", resp.text, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", body)
    leaks = sorted({m.group(0) for m in BARE_KEY.finditer(text)} - TEXT_ALLOWLIST)
    assert not leaks, f"{page} 可见文本泄漏裸键: {leaks}"


def test_templates_have_balanced_attribute_quotes() -> None:
    """模板每行的双引号必须成对——未闭合的属性值会吞掉后续标签。

    浏览器解析器遇到 `title="系统状态>` 这类漏引号时不会报错，而是一路吞到
    下一个 `"` 才闭合，导致中间的标签（图标 <i>/<svg> 等）被当成属性消失，
    JS 的 querySelector 也永远拿不到它。控制台零输出，只能靠静态检查发现。
    """
    offenders: list[str] = []
    for tpl in sorted(TEMPLATES_DIR.glob("*.html")):
        for lineno, line in enumerate(tpl.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            # 注释行与 Jinja 控制行不参与属性解析，跳过避免误报
            if not stripped or stripped.startswith("<!--") or stripped.startswith("{%"):
                continue
            if stripped.count('"') % 2 == 1:
                offenders.append(f"{tpl.name}:{lineno}: {stripped[:100]}")
    assert not offenders, "属性引号未闭合（会静默吞掉后续标签）:\n" + "\n".join(offenders)


def test_page_version_follows_pyproject(test_app) -> None:
    """关于页与状态条的版本串必须来自 version.py，不得再硬编码。"""
    from app.integrated_app.version import get_app_version

    version = get_app_version()
    assert version and version != "unknown", f"get_app_version() 返回异常值 {version!r}"

    about = test_app.get("/settings").text
    assert f"v{version}" in about, f"关于页未展示当前版本 v{version}（疑似硬编码回潮）"
    assert "v1.0.0" not in about, "关于页仍残留硬编码 v1.0.0"


def _parse_csp(policy: str) -> dict[str, set[str]]:
    """把 CSP 字符串解析成 {指令: {来源集}}，便于两份策略做包含关系比较。"""
    out: dict[str, set[str]] = {}
    for part in (policy or "").split(";"):
        tokens = part.strip().split()
        if not tokens:
            continue
        out[tokens[0]] = set(tokens[1:])
    return out


def test_csp_response_header_is_not_stricter_than_meta(test_app) -> None:
    """响应头 CSP 必须放行页面 meta 里声明的每一个来源。

    浏览器对 meta 与响应头两份 CSP 取交集：响应头若比 meta 更严，
    就会拦掉页面明确声明并实际使用的资源（标题字体样式表、blob: 视频对比）。
    nonce 是每次请求动态生成的，meta 里存在而响应头里不存在属预期，跳过比较。
    """
    resp = test_app.get("/restore")
    assert resp.status_code == 200

    header_csp = resp.headers.get("content-security-policy", "")
    meta_match = re.search(r'<meta http-equiv="Content-Security-Policy" content="([^"]+)"', resp.text)
    assert meta_match, "页面未声明 CSP meta"
    assert header_csp, "响应未携带 CSP 响应头"

    header = _parse_csp(header_csp)
    meta = _parse_csp(meta_match.group(1))

    missing: list[str] = []
    for directive, sources in meta.items():
        allowed = header.get(directive)
        if allowed is None:
            # 响应头未声明该指令 → 回退到 default-src，meta 显式声明即视为缺口
            missing.append(f"{directive}: meta 声明 {sorted(sources)}，响应头缺失（回退 default-src）")
            continue
        # nonce 形如 'nonce-xxx'，每次请求都不同，不参与比较
        gap = {s for s in sources if s not in allowed and not s.startswith("'nonce-")}
        if gap:
            missing.append(f"{directive}: meta 放行 {sorted(gap)}，响应头未放行")
    assert not missing, "CSP 响应头比页面 meta 更严，会拦掉页面自己声明并使用的资源：\n" + "\n".join(missing)


def test_history_stat_label_matches_aggregated_field() -> None:
    """统计卡标签必须与后端字段口径一致。

    /api/system/history/statistics 的 total_records 统计的是全部记录（含失败），
    标签若写「完成任务总数」就是自相矛盾（两条失败记录会显示「2 完成任务总数」）。
    """
    data = _load_all()
    for lang in LANGS:
        label = data[lang]["history"]["stat_total_records"]
        # 判定收进变量：长 assert 落在 black 与 ruff-format 的分歧点上，而本仓
        # pre-commit 跑 ruff format、precheck/pre-push 跑 black --check，会来回翻转。
        overstated = any(w in label for w in ("完成", "Completed", "terminées"))
        assert not overstated, f"{lang}: stat_total_records 标签 {label!r} 把全量记录说成完成数"


#: 第三方域名样式表只允许由 JS 按需注入，不允许出现在模板里阻塞页面加载。
_BLOCKING_EXT_CSS = re.compile(r"""<link[^>]+href=["']https?://[^"']+["'][^>]*\brel=["']stylesheet["']""", re.I)


def test_templates_have_no_render_blocking_third_party_stylesheet() -> None:
    """模板里禁止出现指向第三方域名的 `rel="stylesheet"` 阻塞外链。

    根因实测：`base.html` 曾有一条 15 个字体族的 Google Fonts 阻塞 `<link>`。
    把该域名网络黑洞化后，`/restore` **连 DOMContentLoaded 都等不到**（>30s 超时），
    而正常网络下 `load` 仅 176ms。后果不是测试 flake 而是真实产品缺陷：
    弱网 / 该域名不可达环境（对本项目的主要中文用户很常见）会看到整页卡死。
    装饰字体现已改由 `app.js` 在用户打开字体菜单时按需注入。
    """
    offenders: list[str] = []
    for tpl in sorted(TEMPLATES_DIR.rglob("*.html")):
        text = tpl.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            if _BLOCKING_EXT_CSS.search(line):
                offenders.append(f"{tpl.name}:{lineno}")
    assert not offenders, "模板存在渲染阻塞的第三方样式表（应改为 JS 按需注入）：" + ", ".join(offenders)


def test_csp_permits_on_demand_webfont_origins(test_app) -> None:
    """CSP 必须放行 app.js 按需注入的装饰字体源。

    反向风险：字体外链从模板挪进 JS 后，静态审查容易误以为「页面不再依赖
    Google Fonts」而把 CSP 收紧——那会让字体选择器静默失败，14 个选项全部
    退化成系统字体，变成用户点了没反应的假功能。此断言把 CSP 与 JS 常量绑死。
    """
    js = (TEMPLATES_DIR.parent / "static" / "js" / "app.js").read_text(encoding="utf-8")
    m = re.search(r"WEBFONT_CSS\s*=\s*'(https?://[^']+)'", js)
    assert m, "app.js 里找不到 WEBFONT_CSS 常量（按需注入被改掉了？同步更新本用例）"
    hosts = set(re.findall(r"https?://([^/']+)", m.group(1)))
    assert hosts, "WEBFONT_CSS 未解析出任何主机"
    # 样式表来自 googleapis，真正的 woff2 由 Google 重定向到 gstatic（对应 CSP font-src）
    hosts |= {"fonts.gstatic.com"}

    page = test_app.get("/")
    meta = re.search(r'<meta http-equiv="Content-Security-Policy" content="([^"]+)"', page.text)
    assert meta, "首页缺少 CSP meta"
    policy = meta.group(1)
    for host in hosts:
        assert host in policy, f"CSP 未放行按需字体源 {host}（会让标题字体选择器静默失效）：{policy[:200]}"
