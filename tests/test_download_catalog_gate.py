#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""Issue #211 hikari668 ④：下载清单必须随包，且 runtime 段可解析。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "verify_release_integrity.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("seedvr2_verify_integrity_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_missing_catalog_reported(tmp_path):
    module = _load_module()
    problems = module.check_download_catalog(tmp_path)
    assert problems and "缺少下载清单" in problems[0]


def test_runtime_volumes_require_sha256(tmp_path):
    module = _load_module()
    (tmp_path / "downloads_catalog.json").write_text(
        json.dumps({"runtime": {"install_mode": "archive", "volumes": [{"name": "a.7z.001"}]}}),
        encoding="utf-8",
    )
    problems = module.check_download_catalog(tmp_path)
    assert problems and "sha256" in problems[0]


def test_valid_catalog_passes(tmp_path):
    module = _load_module()
    (tmp_path / "downloads_catalog.json").write_text(
        json.dumps(
            {
                "runtime": {
                    "install_mode": "archive",
                    "volumes": [{"name": "runtime.7z.001", "sha256": "0" * 64}],
                }
            }
        ),
        encoding="utf-8",
    )
    assert module.check_download_catalog(tmp_path) == []


def test_online_packages_must_be_string_list(tmp_path):
    module = _load_module()
    (tmp_path / "downloads_catalog.json").write_text(
        json.dumps({"runtime": {"install_mode": "online", "packages": "torch"}}),
        encoding="utf-8",
    )
    assert "packages" in module.check_download_catalog(tmp_path)[0]


def test_unknown_install_mode_rejected(tmp_path):
    module = _load_module()
    (tmp_path / "downloads_catalog.json").write_text(
        json.dumps({"runtime": {"install_mode": "mystery", "volumes": []}}),
        encoding="utf-8",
    )
    assert "install_mode" in module.check_download_catalog(tmp_path)[0]


def test_volume_name_must_be_safe_filename(tmp_path):
    module = _load_module()
    (tmp_path / "downloads_catalog.json").write_text(
        json.dumps(
            {"runtime": {"install_mode": "archive", "volumes": [{"name": "../runtime.7z.001", "sha256": "0" * 64}]}}
        ),
        encoding="utf-8",
    )
    assert "name" in module.check_download_catalog(tmp_path)[0]


def test_volume_sha256_must_be_hex_string(tmp_path):
    module = _load_module()
    (tmp_path / "downloads_catalog.json").write_text(
        json.dumps({"runtime": {"install_mode": "archive", "volumes": [{"name": "runtime.7z.001", "sha256": 123}]}}),
        encoding="utf-8",
    )
    assert "sha256" in module.check_download_catalog(tmp_path)[0]
