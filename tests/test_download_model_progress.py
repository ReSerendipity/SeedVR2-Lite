#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""Issue #211：模型下载必须把进度回传给调用方，并支持 cn/intl 线路透传。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "download_model.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("seedvr2_download_model_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_progress_cb_aggregates_and_source_passed(monkeypatch, tmp_path):
    module = _load_module()
    captured: dict[str, object] = {}

    def fake_download_file(repo, filename, save_dir, **kwargs):
        captured.update(kwargs)
        captured["repo"] = repo
        (Path(save_dir) / filename).write_bytes(b"x")
        callback = kwargs.get("progress_cb")
        if callback:
            callback(0.25, filename)
            callback(0.75, filename)
        return True

    monkeypatch.setattr(module, "_download_file", fake_download_file)
    progress: list[tuple[float, str]] = []
    module.download_model(
        model_size="3b",
        save_dir=str(tmp_path),
        only_files=["seedvr2_ema_3b_fp16.safetensors"],
        with_vae=False,
        verify_hashes=False,
        progress_cb=lambda ratio, label: progress.append((round(ratio, 3), label)),
        source="cn",
        endpoint="https://hf-mirror.com",
    )

    assert captured["source"] == "cn"
    assert captured["endpoint"] == "https://hf-mirror.com"
    assert progress[0] == (0.25, "seedvr2_ema_3b_fp16.safetensors")
    assert progress[-1] == (1.0, "seedvr2_ema_3b_fp16.safetensors"), "每个文件结束必须补齐到整批比例"
    assert all(0.0 <= ratio <= 1.0 for ratio, _ in progress)


def test_unknown_source_rejected(tmp_path):
    module = _load_module()
    with pytest.raises(ValueError):
        module.download_model(save_dir=str(tmp_path), only_files=["x"], source="moon")


def test_hf_fallback_moves_subfolder_file_to_flat_target(monkeypatch, tmp_path):
    module = _load_module()

    def fail_direct(*args, **kwargs):
        raise OSError("direct download blocked")

    def fake_hub_download(repo_id, filename, local_dir):
        path = Path(local_dir) / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"weights")
        return str(path)

    monkeypatch.setattr(module, "_http_download", fail_direct)
    monkeypatch.setattr("huggingface_hub.hf_hub_download", fake_hub_download)

    assert (
        module._download_file(
            "org/repo",
            "weights.safetensors",
            tmp_path,
            subfolder="diffusion_models",
            source="intl",
            endpoint="https://example.invalid",
        )
        is True
    )
    assert (tmp_path / "weights.safetensors").read_bytes() == b"weights"
    assert not (tmp_path / "diffusion_models" / "weights.safetensors").exists()


def test_hf_fallback_respects_cancel_after_download(monkeypatch, tmp_path):
    module = _load_module()
    checks = iter([False, True])

    def fail_direct(*args, **kwargs):
        raise OSError("direct download blocked")

    def fake_hub_download(repo_id, filename, local_dir):
        path = Path(local_dir) / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"weights")
        return str(path)

    monkeypatch.setattr(module, "_http_download", fail_direct)
    monkeypatch.setattr("huggingface_hub.hf_hub_download", fake_hub_download)

    with pytest.raises(InterruptedError):
        module._download_file(
            "org/repo",
            "weights.safetensors",
            tmp_path,
            subfolder="vae",
            source="intl",
            endpoint="https://example.invalid",
            should_cancel=lambda: next(checks),
        )
    assert not (tmp_path / "weights.safetensors").exists()
