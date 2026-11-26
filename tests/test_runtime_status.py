"""Issue #211：瘦包自带的陈旧 RUNTIME.json 不能把空 bootstrap 误报为已安装。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.integrated_app import download_service as ds

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows packaged runtime layout")


def _fake_runtime(root: Path, *, torch: bool) -> None:
    bootstrap = root / "bootstrap"
    (bootstrap / "Lib" / "site-packages").mkdir(parents=True)
    (bootstrap / ds._exe_name()).write_bytes(b"")
    (bootstrap / "RUNTIME.json").write_text(
        json.dumps({"python": "3.12.10", "torch": "2.10.0+cu128", "cuda": "cu128"}),
        encoding="utf-8",
    )
    if torch:
        torch_dir = bootstrap / "Lib" / "site-packages" / "torch"
        torch_dir.mkdir()
        (torch_dir / "__init__.py").write_text("", encoding="utf-8")


def test_stale_meta_without_torch_is_not_installed(tmp_path, monkeypatch):
    _fake_runtime(tmp_path, torch=False)
    monkeypatch.setattr(ds, "PROJECT_ROOT", str(tmp_path))

    status = ds.build_runtime_status()

    assert status["present"] is True
    assert status["torch_present"] is False
    assert status["installed"] is False


def test_meta_with_torch_is_installed(tmp_path, monkeypatch):
    _fake_runtime(tmp_path, torch=True)
    monkeypatch.setattr(ds, "PROJECT_ROOT", str(tmp_path))

    status = ds.build_runtime_status()

    assert status["torch_present"] is True
    assert status["installed"] is True
