"""CSP S4 Phase 2 契约测试（WO-B17 · 2026-11-09）

背景：`docs/CSP_TIGHTENING_ROADMAP.md` §2c/§2d —— Phase 1 已把 display 收口到
`SvVisibility`（`.sv-hidden` + `.sv-vis-*`）；本阶段（Phase 2）把其余内联
`style="..."`、`<style>` 块与 JS 拼装的体裁样式收到 `static/css/style.css`。

本文件把「迁移是否真的完成」「迁移是否视觉等价」「CSP 是否被过早收紧」三件事
变成可执行断言：

1. 静态面：模板/UI JS 零 `style=` 属性、零 `setAttribute('style'`、零 `<style>` 块；
2. 渲染面：六个页面服务端渲染结果同样零内联样式；
3. 迁移锁：每个新类在 style.css 中的声明值必须与迁移前内联声明**逐项一致**
   （值原样照抄是「视觉行为不变」的可执行证据，防止后续清理时误删声明）；
4. 闸门：`style-src` 已在 meta 与响应头两侧同步去掉 `'unsafe-inline'`（2026-11-09 决策），
   用例锁定「两侧都不许放回」+「script-src 的 nonce/回退语义未被顺带改动」+「字体域仍在」；
   `desktop/src/desktop-bridge.js` 的更新弹窗仍用 20 处内联 style（该文件超出本单允许改动
   清单），以 strict-xfail 用例挂账：迁移它之后用例会 XPASS 判红，提示删标记。
5. 负控：内联样式扫描器、`<style>` 块扫描器、`style-src` 判定谓词各自都有「注入即判红」的
   用例，证明上面的「零」「收紧」不是空转。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.integrated_app.app_server import create_app

_REPO_ROOT = Path(__file__).resolve().parents[1]
_APP = _REPO_ROOT / "app" / "integrated_app"
_TEMPLATES = _APP / "templates"
_JS = _APP / "static" / "js"
_CSS = _APP / "static" / "css" / "style.css"
_BRIDGE = _REPO_ROOT / "desktop" / "src" / "desktop-bridge.js"

_PAGES = ["/", "/restore", "/settings", "/history", "/system-status", "/downloads"]

# 内联样式属性：排除 font-style / list-style 这类以 style 结尾的属性名
_INLINE_ATTR_RE = re.compile(r'(?<![A-Za-z0-9_$-])style\s*=\s*["\']')
_STYLE_BLOCK_RE = re.compile(r"<style[ >]", re.I)
_SET_STYLE_ATTR_RE = re.compile(r'setAttribute\s*\(\s*["\']style')


def _find_inline_attrs(text: str) -> list[str]:
    """返回 ``文件行号: 片段`` 形式的内联 style 命中清单。"""
    hits = []
    for i, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith(("//", "*", "<!--")):
            continue
        for m in _INLINE_ATTR_RE.finditer(line):
            hits.append(f"{i}: {stripped[:110]} (col {m.start()})")
    return hits


def _ui_sources() -> list[Path]:
    return [_JS / "app.js", _JS / "video-compare.js", _JS / "visibility.js", *_TEMPLATES.glob("*.html")]


def _strip_at_media(css: str) -> str:
    """剔除所有 ``@media ... { ... }`` 块（按花括号配平），只留顶层规则。

    迁移锁比对的是「无条件生效的声明」；媒体查询里的同名选择器是断点覆盖，
    留在里面会把后置覆盖误读成断点值（设置页分栏比例就踩这个坑）。
    """
    out = []
    i = 0
    while i < len(css):
        at = css.find("@media", i)
        if at < 0:
            out.append(css[i:])
            break
        out.append(css[i:at])
        brace = css.find("{", at)
        if brace < 0:
            break
        depth = 0
        j = brace
        while j < len(css):
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        i = j + 1
    return "".join(out)


def _rule_declarations(css: str, selector: str, *, strip_media: bool = True) -> dict[str, str]:
    """取 ``selector { ... }`` 的最后一次声明块（CSS 后置覆盖语义）。

    返回 ``{属性: 值}``，属性小写、值仅压缩空白，不做其它归一化。
    """
    src = _strip_at_media(css) if strip_media else css
    pattern = re.compile(re.escape(selector) + r"\s*\{([^}]*)\}")
    blocks = pattern.findall(src)
    assert blocks, f"style.css 中找不到选择器 {selector}"
    decls: dict[str, str] = {}
    for raw in blocks[-1].split(";"):
        if ":" not in raw:
            continue
        prop, _, value = raw.partition(":")
        decls[prop.strip().lower()] = " ".join(value.split())
    return decls


def _norm(value: str) -> str:
    return re.sub(r"\s+", "", value).lower()


@pytest.fixture(scope="module")
def client():
    app = create_app({})
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# 1. 静态面
# ---------------------------------------------------------------------------


class TestNoInlineStyleSurface:
    """模板与 UI JS 不得再出现内联样式属性 / 样式块 / setAttribute('style')"""

    def test_templates_have_no_inline_style_attrs(self):
        offenders = []
        for f in sorted(_TEMPLATES.glob("*.html")):
            for hit in _find_inline_attrs(f.read_text(encoding="utf-8")):
                offenders.append(f"{f.name}:{hit}")
        assert offenders == [], "模板仍有内联 style 属性：\n" + "\n".join(offenders)

    def test_templates_have_no_style_blocks(self):
        offenders = []
        for f in sorted(_TEMPLATES.glob("*.html")):
            text = f.read_text(encoding="utf-8")
            for i, line in enumerate(text.splitlines(), 1):
                if _STYLE_BLOCK_RE.search(line):
                    offenders.append(f"{f.name}:{i} {line.strip()[:100]}")
        assert offenders == [], "模板仍有页内 <style> 块（style-src 收紧后会被整块拦下）：\n" + "\n".join(
            offenders
        )

    def test_ui_js_has_no_inline_style_markup(self):
        """UI JS 拼装的 innerHTML 同样受 style-src 管辖（CSSOM 写入不受限，另计）"""
        offenders = []
        for f in _ui_sources():
            text = f.read_text(encoding="utf-8")
            for hit in _find_inline_attrs(text):
                offenders.append(f"{f.name}:{hit}")
            for i, line in enumerate(text.splitlines(), 1):
                if _SET_STYLE_ATTR_RE.search(line):
                    offenders.append(f"{f.name}:{i} setAttribute('style') {line.strip()[:90]}")
        assert offenders == [], "UI JS 仍拼接内联 style / setAttribute('style')：\n" + "\n".join(offenders)

    def test_ui_js_cssom_writes_are_the_only_remaining_style_usage(self):
        """CSSOM 写入（el.style.x / cssText）不受 style-src 限制，允许保留；
        这里只锁定其存在量级，避免有人把「允许」误读成「可以随便写内联属性」。"""
        sources = "\n".join(
            line
            for f in _ui_sources()
            for line in f.read_text(encoding="utf-8").splitlines()
            if not line.strip().startswith(("//", "*", "<!--"))
        )
        assert not _INLINE_ATTR_RE.search(sources), "UI 源码（非注释）仍含内联 style 属性"
        cssom = re.findall(r"\.style\.(?!cssText)", sources)
        assert len(cssom) > 0, "未检出任何 CSSOM 写入，可能扫描口径写错了"


# ---------------------------------------------------------------------------
# 2. 渲染面
# ---------------------------------------------------------------------------


class TestRenderedPages:
    """服务端渲染结果必须干净（同时验证 Jinja 渲染没被改坏）"""

    def test_pages_render_without_inline_style(self, client):
        for path in _PAGES:
            r = client.get(path)
            assert r.status_code == 200, f"{path} 渲染失败：{r.status_code}"
            hits = _find_inline_attrs(r.text)
            assert hits == [], f"{path} 渲染结果仍含内联 style：{hits[:5]}"

    def test_pages_render_without_style_block(self, client):
        for path in _PAGES:
            html = client.get(path).text
            assert not _STYLE_BLOCK_RE.search(html), f"{path} 渲染结果仍含 <style> 块"


# ---------------------------------------------------------------------------
# 3. 迁移锁：新类的声明值必须与迁移前内联逐项一致
# ---------------------------------------------------------------------------

# (选择器, {属性: 迁移前内联值})
_MIGRATION_LOCK: list[tuple[str, dict[str, str]]] = [
    (".sv-font-dd-glyph", {"font-weight": "700", "font-style": "italic", "font-size": "15px"}),
    (".sv-bar-fill-zero", {"transform": "scaleX(0)", "transform-origin": "left center"}),
    (".sv-recent-empty-actions", {"margin-top": "12px"}),
    (".sv2-btn-retry", {"height": "38px", "padding": "0 18px"}),
    (
        ".sv2-result-media",
        {
            "max-width": "calc(100% - 40px)",
            "max-height": "calc(100% - 40px)",
            "border-radius": "var(--sv-radius)",
            "box-shadow": "0 10px 40px rgba(0, 0, 0, .3)",
        },
    ),
    (".sv2-result-img", {"object-fit": "contain"}),
    (".sv2-allow-dirs", {"margin-bottom": "10px"}),
    (
        ".sv2-allow-dirs-body",
        {
            "margin-top": "6px",
            "padding": "8px 10px",
            "background": "var(--sv-surface-2)",
            "border-radius": "var(--sv-radius-sm)",
        },
    ),
    (".sv2-vae-tier-hint", {"margin-left": "8px", "font-weight": "400"}),
    (".sv2-agreement-modal", {"max-width": "560px"}),
    (".sv2-agreement-title", {"margin": "0 0 10px"}),
    (".sv2-agreement-text", {"font-size": "13.5px", "line-height": "1.7", "margin": "0 0 8px"}),
    (
        ".sv2-agreement-list",
        {"font-size": "13px", "line-height": "1.7", "margin": "0 0 10px", "padding-left": "18px"},
    ),
    (".sv2-agreement-ack", {"width": "100%", "margin-top": "6px"}),
    (".sv2-agreement-ack:disabled", {"opacity": ".5"}),
    (".sv2-scan-flex1", {"flex": "1"}),
    (".sv2-scan-check", {"color": "#28a745"}),
    (".sv2-scan-total", {"font-size": "1rem"}),
    (".sv2-scan-stats", {"color": "#666"}),
    (
        ".sv-batch-file-list-scanned",
        {
            "max-height": "280px",
            "overflow-y": "auto",
            "border": "1px solid #e0e0e0",
            "border-radius": "6px",
            "padding": "8px",
            "background": "#fafafa",
        },
    ),
    (".sv2-allow-dir-del", {"margin-left": "auto", "flex-shrink": "0"}),
    (".sv-settings-paths-note", {"font-size": "12px"}),
    (".sv-about-faq-card-body", {"padding": "0"}),
    (".sv-about-faq-item", {"border-bottom": "1px solid var(--sv-border-light)", "padding": "14px 16px"}),
    (
        ".sv-about-faq-chevron",
        {"transition": "transform .2s", "color": "var(--sv-primary)", "flex-shrink": "0"},
    ),
    (
        ".sv-about-faq-answer",
        {
            "font-size": "12px",
            "color": "var(--sv-text-muted)",
            "line-height": "1.6",
            "margin-top": "10px",
            "margin-bottom": "0",
            "padding-left": "22px",
        },
    ),
    (".sv-gauge-fill-zero", {"width": "0%"}),
    (".sv-vram-num", {"max-width": "80px"}),
    (".sv-modal.sv-modal-sm", {"max-width": "420px"}),
    (".sv-clear-history-text", {"margin-bottom": "14px"}),
    (
        ".sv-clear-history-chk",
        {"width": "18px", "height": "18px", "accent-color": "var(--sv-primary)"},
    ),
    (".sv-clear-history-label", {"font-size": "0.85rem"}),
    # 页内 <style> 块迁入的两段（顺序敏感：设置页分栏比例必须仍是后置覆盖）
    (
        ".sv-settings-layout",
        {"display": "grid", "grid-template-columns": "1fr 1.2fr", "gap": "24px", "align-items": "start"},
    ),
    (".sv-settings-col", {"display": "flex", "flex-direction": "column", "gap": "24px", "min-width": "0"}),
    (".dl-summary", {"display": "flex", "align-items": "center", "gap": "12px"}),
    (".dl-kv", {"display": "grid", "grid-template-columns": "110px 1fr"}),
]


class TestMigrationLock:
    """迁移锁：类声明值 == 迁移前内联声明值（逐项比对）"""

    def test_every_migrated_class_keeps_original_declarations(self):
        css = _CSS.read_text(encoding="utf-8")
        failures = []
        for selector, expected in _MIGRATION_LOCK:
            actual = _rule_declarations(css, selector)
            for prop, want in expected.items():
                got = actual.get(prop)
                if got is None:
                    failures.append(f"{selector}: 缺少 {prop}: {want}")
                elif _norm(got) != _norm(want):
                    failures.append(f"{selector}: {prop} 期望 {want!r} 实际 {got!r}")
        assert failures == [], "迁移后的类声明与原内联不一致（视觉会变）：\n" + "\n".join(failures)

    def test_settings_layout_rule_is_last_definition(self):
        """设置页分栏比例原由页内 <style> 后置覆盖；迁到 style.css 后必须仍在
        基础的 .sv-settings-layout{180px 1fr} 之后，否则设置页退回旧分栏。"""
        css = _strip_at_media(_CSS.read_text(encoding="utf-8"))
        defs = [m.start() for m in re.finditer(r"\.sv-settings-layout\s*\{", css)]
        assert len(defs) == 2, f"顶层应有基础与迁移两处定义，实际 {len(defs)} 处"
        last = _rule_declarations(css, ".sv-settings-layout", strip_media=False)
        assert _norm(last["grid-template-columns"]) == _norm("1fr 1.2fr")
        assert _norm(_rule_declarations(css, ".sv-settings-layout")["display"]) == "grid"

    @pytest.mark.parametrize("page,template", [("downloads.html", "downloads.html"), ("settings.html", "settings.html")])
    def test_page_scoped_classes_have_rules(self, page, template):
        """页内 <style> 迁出后，页面用到的私有类必须在 style.css 里都有定义。"""
        css = _CSS.read_text(encoding="utf-8")
        html = (_TEMPLATES / template).read_text(encoding="utf-8")
        prefixes = {"downloads.html": "dl-", "settings.html": "sv-settings-"}
        prefix = prefixes[page]
        used = {c for c in re.findall(r'class="([^"]*)"', html) for c in c.split() if c.startswith(prefix)}
        if page == "downloads.html":
            # 这些类由 JS 拼装的列表项引入，一并纳入
            used |= set(re.findall(r"\b(dl-[a-z-]+)\b", html))
        missing = sorted(c for c in used if not re.search(r"\." + re.escape(c) + r"\s*(,|\{)", css))
        assert missing == [], f"{page} 迁移后仍有类在 style.css 中无定义：{missing}"


# ---------------------------------------------------------------------------
# 4. 闸门：`style-src` 已两侧同步收紧（2026-11-09 决策），必须持续不含 unsafe-inline
# ---------------------------------------------------------------------------


def _style_src_of(csp_text: str) -> str:
    """取 CSP 文本里的 style-src 指令值；缺失即判失败（收紧后不允许隐式回退）。"""
    m = re.search(r"style-src ([^;]+)", csp_text)
    assert m, f"CSP 里找不到 style-src：{csp_text[:120]}"
    return m.group(1)


def _assert_style_src_tight(csp_text: str, where: str) -> None:
    """闸门本体：任何一处 CSP 文本的 style-src 都不许含 `'unsafe-inline'`。"""
    style_src = _style_src_of(csp_text)
    assert "unsafe-inline" not in style_src, f"{where} 的 style-src 放回了 'unsafe-inline'：{style_src}"


class TestS4Gate:
    """S4 闸门（WO-B17 决策后）：`style-src` 已收紧，两处（meta + 响应头）必须持续一致，
    'self' 与字体域（决策 D22）必须保留。"""

    def test_app_side_is_clear(self):
        hits = []
        for f in _ui_sources():
            hits += _find_inline_attrs(f.read_text(encoding="utf-8"))
        assert hits == [], "应用侧（模板/UI JS）仍有内联样式，Phase 2 未完成"

    def test_rendered_meta_style_src_is_tightened(self, client):
        for path in _PAGES:
            _assert_style_src_tight(client.get(path).text, f"{path} 的 meta")

    def test_response_header_style_src_is_tightened(self, client):
        """meta 与响应头取交集，一侧放回 unsafe-inline 都算回退"""
        header = client.get("/").headers.get("content-security-policy", "")
        assert header, "响应头缺 Content-Security-Policy"
        _assert_style_src_tight(header, "响应头")

    def test_gate_catches_header_regression(self, monkeypatch):
        """端到端负控：把响应头侧策略改回含 'unsafe-inline'，同一闸门必须判红——
        证明上面两条绿断言咬住的是真实响应头，不是空转。"""
        from app.integrated_app.middleware import security_headers as sh

        loose = sh._DEFAULT_CSP.replace(
            "style-src 'self' https://fonts.googleapis.com",
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        )
        assert loose != sh._DEFAULT_CSP, "负控注入未生效，测试无效"
        monkeypatch.setattr(sh, "_DEFAULT_CSP", loose)
        with TestClient(create_app({})) as c:
            header = c.get("/").headers.get("content-security-policy", "")
        with pytest.raises(AssertionError):
            _assert_style_src_tight(header, "负控响应头")

    def test_script_src_fallback_is_untouched(self, client):
        """本单只动 style-src：script-src 的 nonce/回退语义与 S2 交付时一致"""
        header = client.get("/").headers.get("content-security-policy", "")
        assert "script-src 'self' 'unsafe-inline'" in header

    def test_style_src_keeps_self_and_font_domain(self, client):
        """字体菜单按需插入 Google Fonts 样式表，收紧时不得误删该源（S3 决策 D22）"""
        header_style_src = _style_src_of(client.get("/").headers.get("content-security-policy", ""))
        assert "'self'" in header_style_src
        assert "https://fonts.googleapis.com" in header_style_src

    def test_style_src_negative_control(self):
        """负控：判定谓词对「放回 unsafe-inline」的 CSP 必须判红，否则上面的绿是空转"""
        loose = "default-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com;"
        assert "unsafe-inline" in _style_src_of(loose)
        tight = "default-src 'self'; style-src 'self' https://fonts.googleapis.com;"
        assert "unsafe-inline" not in _style_src_of(tight)

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "已知偏差：desktop/src/desktop-bridge.js 的更新弹窗仍用 20 处内联 style 拼 DOM"
            "（经 initialization_script 注入后端页面，受页面 style-src 管辖）；该文件不在 WO-B17"
            "允许改动清单内，故 `style-src` 先按决策收紧。迁移该文件后本用例会 XPASS，"
            "strict 会判红——届时删掉本标记，断言原样保留为硬判据。"
        ),
    )
    def test_desktop_bridge_inline_styles_are_migrated(self):
        assert _find_inline_attrs(_BRIDGE.read_text(encoding="utf-8")) == []


# ---------------------------------------------------------------------------
# 5. 负控：扫描器必须能抓住注入的内联样式
# ---------------------------------------------------------------------------


class TestScannerNegativeControl:
    """负控：证明「零内联样式」的断言不是空转"""

    def test_scanner_detects_injected_inline_style(self, tmp_path):
        probe = tmp_path / "probe.html"
        probe.write_text('<div class="sv-card" style="margin-top:12px">x</div>\n', encoding="utf-8")
        hits = _find_inline_attrs(probe.read_text(encoding="utf-8"))
        assert hits, "负控未判红：注入的内联 style 未被扫描器发现，断言无效"

    def test_scanner_ignores_style_suffix_attributes(self, tmp_path):
        """font-style / list-style 等以 style 结尾的属性不应被误判（假红防线）"""
        probe = tmp_path / "ok.html"
        probe.write_text('<text font-style="italic">S</text>\n', encoding="utf-8")
        assert _find_inline_attrs(probe.read_text(encoding="utf-8")) == []

    def test_scanner_detects_style_block(self, tmp_path):
        probe = tmp_path / "block.html"
        probe.write_text("<style>\n.dl-x{color:red}\n</style>\n", encoding="utf-8")
        text = probe.read_text(encoding="utf-8")
        assert any(_STYLE_BLOCK_RE.search(line) for line in text.splitlines())
