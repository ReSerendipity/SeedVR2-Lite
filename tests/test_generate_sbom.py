"""SBOM 生成器测试（软件供应链安全 P1#2 落地）。

覆盖：续行/单行两种锁排版解析、多哈希保序去重、注释跳过、非法行报错、
排序与 purl 小写、serialNumber 幂等、固定时间戳下确定性、真实锁冒烟。
不联网；真实锁只读。
"""

from __future__ import annotations

import pytest

from scripts.generate_sbom import build_bom, parse_lock

# 混排夹具：注释 + 单行钉版 + 续行钉版（与真实锁同形）
FIXTURE_LOCK = """\
# SeedVR2 依赖哈希锁文件 (CWE-912 供应链投毒防御)
#
fastapi==0.118.0 \\
    --hash=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \\
    --hash=sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
PyWavelets==1.9.0 --hash=sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc
uvicorn==0.37.0 --hash=sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd
"""


def _by_name(components: list[dict], name: str) -> dict:
    return next(c for c in components if c["name"].lower() == name)


class TestParseLock:
    def test_sorted_case_insensitive_and_hashes_in_order(self):
        comps = parse_lock(FIXTURE_LOCK)
        assert [c["name"] for c in comps] == ["fastapi", "PyWavelets", "uvicorn"]
        fastapi = _by_name(comps, "fastapi")
        assert fastapi["version"] == "0.118.0"
        assert [h["content"][:8] for h in fastapi["hashes"]] == ["aaaaaaaa", "bbbbbbbb"]
        assert all(h["alg"] == "SHA-256" for h in fastapi["hashes"])

    def test_purl_uses_lowercase_name(self):
        comps = parse_lock(FIXTURE_LOCK)
        assert _by_name(comps, "pywavelets")["purl"] == "pkg:pypi/pywavelets@1.9.0"

    def test_parses_pip_continuation_lines(self):
        """行尾反斜杠续行（真实锁排版）：多哈希保序合并。"""
        cont = (
            "Authlib==1.8.0 \\\n"
            "    --hash=sha256:eeee \\\n"
            "    --hash=sha256:ffff\n"
            "torch==2.13.0+cu132 --hash=sha256:abab\n"
        )
        comps = parse_lock(cont)
        assert [c["name"] for c in comps] == ["Authlib", "torch"]
        authlib = comps[0]
        assert authlib["version"] == "1.8.0"
        assert [h["content"] for h in authlib["hashes"]] == ["eeee", "ffff"]

    def test_duplicate_hashes_deduped(self):
        comps = parse_lock("x==1 --hash=sha256:aa --hash=sha256:aa\n")
        assert len(comps[0]["hashes"]) == 1

    def test_invalid_line_raises(self):
        with pytest.raises(ValueError, match="unparsable"):
            parse_lock("some-package 1.2.3  # not a pin\n")


class TestBuildBom:
    def test_deterministic_with_fixed_timestamp(self):
        bom1 = build_bom(FIXTURE_LOCK, "2026-10-15T00:00:00Z", "1.6.1")
        bom2 = build_bom(FIXTURE_LOCK, "2026-10-15T00:00:00Z", "1.6.1")
        assert bom1 == bom2

    def test_serial_is_stable_urn_uuid(self):
        serial = build_bom(FIXTURE_LOCK, "2026-10-15T00:00:00Z", "1.6.1")["serialNumber"]
        assert serial.startswith("urn:uuid:")
        other = build_bom(FIXTURE_LOCK, "2027-01-01T00:00:00Z", "1.6.1")["serialNumber"]
        assert other == serial

    def test_document_shape(self):
        bom = build_bom(FIXTURE_LOCK, "2026-10-15T00:00:00Z", "1.6.1")
        assert bom["bomFormat"] == "CycloneDX"
        assert bom["specVersion"] == "1.5"
        assert bom["metadata"]["component"]["version"] == "1.6.1"
        assert bom["metadata"]["tools"]["components"][0]["name"] == "seedvr2-generate-sbom"
        assert len(bom["components"]) == 3

    def test_empty_lock_raises(self):
        with pytest.raises(ValueError, match="zero components"):
            build_bom("# only comments\n", "2026-10-15T00:00:00Z", "1.6.1")


class TestRealLockSmoke:
    """真实锁文件冒烟（仓内 requirements-lock.txt，离线只读）。"""

    def test_real_lock_produces_bom(self):
        from pathlib import Path

        lock = Path(__file__).resolve().parent.parent / "requirements-lock.txt"
        bom = build_bom(lock.read_text(encoding="utf-8"), "2026-10-15T00:00:00Z", "1.6.1")
        names = {c["name"].lower() for c in bom["components"]}
        assert {"fastapi", "torch", "uvicorn"} <= names
        torch = _by_name(bom["components"], "torch")
        assert torch.get("hashes"), "torch 必须带哈希锚点"


if __name__ == "__main__":
    pytest.main([__file__, "-q", "-p", "no:cacheprovider"])
