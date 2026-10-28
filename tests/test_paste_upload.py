"""粘贴上传（Ctrl+V）接线回归测试。

能力本身在浏览器里（app.js 的 setupPasteUpload），纯前端无法用 pytest 跑真实
剪贴板事件，因此这里守住"接线不会断"的三件事：

1. app.js 定义并导出了 setupPasteUpload（漏导出 → 模板调用即 TypeError）；
2. restore.html 用它接管的是**同一个**上传区与 input（接错元素 → 粘贴无效）；
3. 五套语言包都有 restore.pasted（缺键 → 提示退化成裸键）。

真实交互由 tests/frontend/smoke.js（jsdom）与 Playwright E2E 覆盖渲染层。
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
APP_JS = ROOT / "app/integrated_app/static/js/app.js"
RESTORE_TPL = ROOT / "app/integrated_app/templates/restore.html"
LOCALES_DIR = ROOT / "app/integrated_app/locales"
LANGS = ["zh", "zh-TW", "en", "ja", "fr"]


def test_app_js_defines_and_exports_paste_helper() -> None:
    js = APP_JS.read_text(encoding="utf-8")
    assert re.search(r"function setupPasteUpload\(", js), "app.js 未定义 setupPasteUpload"
    assert re.search(
        r"^\s+setupPasteUpload,\s*$", js, re.M
    ), "setupPasteUpload 未导出到 SeedVR2 命名空间（模板调用会 TypeError）"


def test_paste_helper_guards_text_input_focus() -> None:
    """焦点在输入框/可编辑区时必须放行原生粘贴，不能抢用户粘文字。"""
    js = APP_JS.read_text(encoding="utf-8")
    start = js.index("function setupPasteUpload(")
    body = js[start : start + 2600]
    assert "isContentEditable" in body, "缺少可编辑区判断，会劫持富文本输入"
    assert "TEXTAREA" in body and "INPUT" in body, "缺少输入框判断"
    assert "offsetParent === null" in body, "缺少「上传区不可见则不接管」判断"


def test_paste_helper_names_clipboard_file_with_extension() -> None:
    """剪贴板图片常无文件名，必须补成带合法扩展名的名字，否则后端按扩展名拒绝。"""
    js = APP_JS.read_text(encoding="utf-8")
    start = js.index("function setupPasteUpload(")
    body = js[start : start + 2600]
    assert "new File([file]" in body, "未重命名剪贴板文件"
    assert ".png" in body or "png" in body, "未提供扩展名兜底"


def test_restore_page_wires_paste_to_same_upload_zone() -> None:
    html = RESTORE_TPL.read_text(encoding="utf-8")
    assert (
        "SeedVR2.setupPasteUpload(els.uploadZone, els.fileInput" in html
    ), "restore.html 未把粘贴接在上传区上（或接错了元素）"
    # 与拖拽/点选复用同一套回调，避免两条上传路径行为分叉
    assert "restoreFileHandlers" in html, "未抽出共用回调对象，粘贴与拖拽行为会分叉"
    assert "onPasted" in html, "缺少粘贴成功提示"


@pytest.mark.parametrize("lang", LANGS)
def test_locale_has_pasted_message(lang: str) -> None:
    import json

    data = json.loads((LOCALES_DIR / f"{lang}.json").read_text(encoding="utf-8"))
    text = data.get("restore", {}).get("pasted")
    assert isinstance(text, str) and text.strip(), f"{lang}.json 缺少 restore.pasted"
