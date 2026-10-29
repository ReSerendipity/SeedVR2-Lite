"""routes/restore/upload.py 的焦点单测。

补的是 Better Harness finding `targeted-gate-maps-upload-to-front-end-test`：提交层定向
门禁（scripts/post_edit_gate.py 的 match_tests）按 `tests/test_<模块名>.py` 精确命中，
upload.py 一直没有同名测试文件，只能落到模糊命中——唯一命中是 tests/test_paste_upload.py，
而它只用正则扫 app.js / restore.html / 语言包，从不导入本模块。于是「只改 upload.py」时
提交层会报定向用例通过，实际一条本模块用例都没跑。有了本文件，精确命中成立。

覆盖的是本模块自己承诺的两条口径：幂等键校验（P1-4）与 ffmpeg 版本血缘注入（P1-2），
不依赖 GPU 与真实上传文件。
"""

import json

import pytest
from fastapi import HTTPException

from app.integrated_app.routes.restore import upload as up


class _FakeRequest:
    """只带 headers 的请求替身（被测函数只用 request.headers）。"""

    def __init__(self, headers=None):
        self.headers = headers or {}


class TestResolveIdempotencyKey:
    def test_header_wins_over_form(self):
        req = _FakeRequest({"Idempotency-Key": "hdr-key"})
        assert up._resolve_idempotency_key(req, "form-key") == "hdr-key"

    def test_form_fallback_when_no_header(self):
        req = _FakeRequest({})
        assert up._resolve_idempotency_key(req, "form-key") == "form-key"

    def test_absent_key_returns_none(self):
        assert up._resolve_idempotency_key(_FakeRequest({}), None) is None
        assert up._resolve_idempotency_key(_FakeRequest({"Idempotency-Key": "   "}), "  ") is None

    def test_surrounding_whitespace_is_stripped(self):
        req = _FakeRequest({"Idempotency-Key": "  abc-123_x.y  "})
        assert up._resolve_idempotency_key(req, None) == "abc-123_x.y"

    @pytest.mark.parametrize("bad", ["has space", "semi;colon", "slash/x", "中文键", "x" * 65])
    def test_illegal_key_raises_400(self, bad):
        with pytest.raises(HTTPException) as exc:
            up._resolve_idempotency_key(_FakeRequest({"Idempotency-Key": bad}), None)
        assert exc.value.status_code == 400

    def test_max_length_boundary_accepted(self):
        key = "k" * 64
        assert up._resolve_idempotency_key(_FakeRequest({"Idempotency-Key": key}), None) == key


class TestLineageParameters:
    def test_image_task_is_left_untouched(self):
        payload = json.dumps({"seed": 1}, ensure_ascii=False)
        assert up._lineage_parameters(payload, "image") == payload

    def test_video_task_injects_version(self, monkeypatch):
        import app.integrated_app.video_processor as vp

        monkeypatch.setattr(vp, "get_ffmpeg_version", lambda: "8.0")
        out = json.loads(up._lineage_parameters(json.dumps({"seed": 1}), "video"))
        assert out["ffmpeg_version"] == "8.0"
        assert out["seed"] == 1

    def test_non_ascii_is_preserved(self, monkeypatch):
        import app.integrated_app.video_processor as vp

        monkeypatch.setattr(vp, "get_ffmpeg_version", lambda: "8.0")
        raw = json.dumps({"提示词": "你好"}, ensure_ascii=False)
        assert "你好" in up._lineage_parameters(raw, "video")

    def test_missing_ffmpeg_keeps_original_json(self, monkeypatch):
        """ffmpeg 不可用时血缘缺失可接受，绝不影响任务提交（口径见函数 docstring）。"""
        import app.integrated_app.video_processor as vp

        monkeypatch.setattr(vp, "get_ffmpeg_version", lambda: "")
        payload = json.dumps({"seed": 1})
        assert up._lineage_parameters(payload, "video") == payload

    def test_broken_json_does_not_raise(self, monkeypatch):
        """参数 JSON 解析失败时跳过注入并返回原串，不向上抛。"""
        import app.integrated_app.video_processor as vp

        monkeypatch.setattr(vp, "get_ffmpeg_version", lambda: "8.0")
        broken = "{not-json"
        assert up._lineage_parameters(broken, "video") == broken
