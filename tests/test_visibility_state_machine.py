"""可见性状态机契约测试（CSP 收紧 S4 · Phase 1 / 2026-11-09）

背景：`docs/CSP_TIGHTENING_ROADMAP.md` §2c —— 模板里 66 处内联 style（含 26 处
`display:none`）与 JS 侧 98 写 + 11 读 `el.style.display` 是同一件事的两份真相。
内联 display 一旦迁走，读侧 `style.display !== 'none'` 会变成恒真（读到空串），
因此本阶段先把"唯一出口"立起来：

- 隐藏只认 `.sv-hidden`；显示时的 display 值由 `.sv-vis-*` 模式类复现；
- 读取一律 `SvVisibility.isVisible(el)`；
- 模板内联 style 中不得再出现 `display`，UI JS 中不得再出现 `.style.display`。

本文件把这三条变成可执行断言，并用 node 跑真实模块的行为契约（show/hide/
isVisible/模式类/空值安全），另附一条负控证明断言不是空转：
把 `hide()` 的一句实现抹掉后，同一套断言必须判红。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.integrated_app.app_server import create_app

_REPO_ROOT = Path(__file__).resolve().parents[1]
_APP = _REPO_ROOT / "app" / "integrated_app"
_TEMPLATES = _APP / "templates"
_CSS = _APP / "static" / "css" / "style.css"
_JS = _APP / "static" / "js"
_HARNESS = Path(__file__).resolve().parent / "fixtures" / "visibility_state_machine_harness.js"

_PAGES = ["/", "/restore", "/settings", "/history", "/system-status", "/downloads"]

_STYLE_ATTR_RE = re.compile(r'style="([^"]*)"')
_STYLE_DISPLAY_RE = re.compile(r"\.style\.display\b")
_SHOW_MODE_RE = re.compile(r"SvVisibility\.(?:show|set)\([^;\n]*?,\s*'([a-z-]+)'\s*\)")
_TAG_BY_ID_RE = r"<[a-zA-Z][^>]*\bid=\"{id}\"[^>]*>"

_NODE = shutil.which("node")
_requires_node = pytest.mark.skipif(
    _NODE is None,
    reason="本机未安装 node，跳过可见性状态机的 JS 行为契约（Python 侧断言仍生效）",
)

# 迁移后必须带初始 sv-hidden 的元素（模板里原本写死 style="display:none" 的那批）
_INITIALLY_HIDDEN = {
    "/": ["recentEmpty"],
    "/restore": [
        "batchToolbar",
        "tbFileName",
        "restoreFileInfo",
        "imagePreviewContainer",
        "progressCard",
        "progressDetail",
        "resultCard",
        "errorCard",
        "videoPlainViewer",
        "plainViewer",
        "compareCard",
        "videoCompareCard",
        "batchProgressCard",
        "btnRetryBatch",
        "batchCurrentFile",
        "vramRecommendResult",
        "restoreRecWarning",
        "onboardingModal",
        "agreementModal",
        "btnRestoreSidebar",
    ],
    "/system-status": ["vramEstimateResults", "vramRecWarning"],
}


@pytest.fixture(scope="module")
def client():
    """复用同一个 app，避免每个用例重复建图"""
    app = create_app({})
    with TestClient(app) as c:
        yield c


class TestNoInlineDisplaySurface:
    """静态面：模板与 UI JS 里不能再有内联 display"""

    def test_templates_have_no_inline_display(self):
        offenders = []
        for f in sorted(_TEMPLATES.glob("*.html")):
            text = f.read_text(encoding="utf-8")
            for i, line in enumerate(text.splitlines(), 1):
                for m in _STYLE_ATTR_RE.finditer(line):
                    if "display" in m.group(1):
                        offenders.append(f'{f.name}:{i} style="{m.group(1)}"')
        assert offenders == [], "模板仍有内联 display（必须走 .sv-hidden/.sv-vis-*）：\n" + "\n".join(offenders)

    def test_ui_js_has_no_style_display(self):
        """UI JS（app.js / video-compare.js / 模板内联脚本）不得读写 el.style.display"""
        offenders = []
        for f in sorted([_JS / "app.js", _JS / "video-compare.js", *_TEMPLATES.glob("*.html")]):
            text = f.read_text(encoding="utf-8")
            for i, line in enumerate(text.splitlines(), 1):
                if _STYLE_DISPLAY_RE.search(line):
                    offenders.append(f"{f.name}:{i} {line.strip()[:120]}")
        assert offenders == [], "仍有 style.display 读写，必须改走 SvVisibility：\n" + "\n".join(offenders)


class TestCssContract:
    """CSS 面：唯一隐藏机制 + 模式类齐全，且不与既有 !important 打架"""

    def test_hidden_class_is_display_none_important(self):
        css = _CSS.read_text(encoding="utf-8")
        m = re.search(r"\.sv-hidden\s*\{([^}]*)\}", css)
        assert m, "style.css 缺少 .sv-hidden 规则"
        body = m.group(1)
        assert re.search(r"display\s*:\s*none\s*!important", body), ".sv-hidden 必须是 display:none!important"
        assert body.count("display") == 1, ".sv-hidden 只应声明 display:none"

    def test_every_mode_used_by_js_has_a_class(self):
        css = _CSS.read_text(encoding="utf-8")
        sources = ", ".join(f.read_text(encoding="utf-8") for f in [_JS / "app.js", *_TEMPLATES.glob("*.html")])
        modes = sorted(set(_SHOW_MODE_RE.findall(sources)))
        assert modes, "未在 JS 中找到任何 show(el, '<mode>') 调用，模式类无从校验"
        missing = [m for m in modes if not re.search(rf"\.sv-vis-{re.escape(m)}\s*\{{", css)]
        assert missing == [], f"JS 用到的模式类在 style.css 中缺失：{missing}"

    def test_compare_card_display_lost_its_important(self):
        """#compareCard 的 display 若带 !important，会压过 .sv-hidden（id 特异性），
        状态机就永远藏不住它——这也是迁移前对比卡"关不掉"的根因。"""
        css = _CSS.read_text(encoding="utf-8")
        m = re.search(r"#compareCard\s*\{([^}]*)\}", css)
        assert m, "style.css 缺少 #compareCard 规则"
        display_decls = [ln for ln in m.group(1).splitlines() if "display" in ln]
        assert display_decls, "#compareCard 仍应有 display:flex（显示态布局依赖它）"
        assert all(
            "!important" not in ln for ln in display_decls
        ), f"#compareCard display 不得带 !important：{display_decls}"

    def test_state_machine_loads_before_app_js(self):
        base = (_TEMPLATES / "base.html").read_text(encoding="utf-8")
        assert "/static/js/visibility.js" in base, "base.html 未引入 visibility.js"
        assert base.index("visibility.js") < base.index("/static/js/app.js"), "visibility.js 必须先于 app.js 加载"


class TestRenderedPages:
    """渲染面：六个页面都要干净（这一层同时验证 Jinja 渲染没被改坏）"""

    def test_pages_render_and_have_no_inline_display(self, client):
        for path in _PAGES:
            r = client.get(path)
            assert r.status_code == 200, f"{path} 渲染失败：{r.status_code}"
            hits = [m.group(1) for m in _STYLE_ATTR_RE.finditer(r.text) if "display" in m.group(1)]
            assert hits == [], f"{path} 渲染结果仍含内联 display：{hits}"

    def test_rendered_inline_scripts_do_not_touch_style_display(self, client):
        for path in _PAGES:
            html = client.get(path).text
            assert not _STYLE_DISPLAY_RE.search(html), f"{path} 内联脚本仍在读写 style.display"

    def test_migrated_elements_start_hidden(self, client):
        for path, ids in _INITIALLY_HIDDEN.items():
            html = client.get(path).text
            missing = []
            for eid in ids:
                m = re.search(_TAG_BY_ID_RE.format(id=re.escape(eid)), html)
                if not m:
                    missing.append(f"{eid}(标签缺失)")
                elif "sv-hidden" not in m.group(0):
                    missing.append(f"{eid}(缺 sv-hidden)")
            assert missing == [], f"{path} 这些元素的初始隐藏态不对：{missing}"


class TestVisibilityModuleBehaviour:
    """行为面：直接跑真实 visibility.js（node + 最小 FakeEl）"""

    @_requires_node
    def test_module_behaviour_contract(self):
        module = _JS / "visibility.js"
        assert module.exists(), "visibility.js 不存在"
        proc = subprocess.run(
            [_NODE, str(_HARNESS), str(module)],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert proc.returncode == 0, f"行为契约失败：\n{proc.stdout}\n{proc.stderr}"
        assert "PASS 11 checks" in proc.stdout

    @_requires_node
    def test_negative_control_missing_hidden_marker_goes_red(self, tmp_path):
        """负控：把 hide() 里的 `classList.add(HIDDEN_CLASS)` 抹掉，同一套断言必须判红。
        若本用例通过不了（即变异体仍 PASS），说明上面的契约测试是空转。"""
        source = (_JS / "visibility.js").read_text(encoding="utf-8")
        mutated = source.replace(
            "        el.classList.add(HIDDEN_CLASS);",
            "        /* NEGATIVE CONTROL: marker removed */",
            1,
        )
        assert mutated != source, "负控变异未生效（visibility.js 的 hide 实现可能已改动，请同步本用例）"
        target = tmp_path / "visibility_mutated.js"
        target.write_text(mutated, encoding="utf-8")
        proc = subprocess.run(
            [_NODE, str(_HARNESS), str(target)],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert proc.returncode != 0, "负控未判红：抹掉隐藏标记后断言仍全绿，测试无效"
        assert "FAIL:" in proc.stdout, "负控应打印 FAIL 明细"
