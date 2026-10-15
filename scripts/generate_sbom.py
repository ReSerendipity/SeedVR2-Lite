#!/usr/bin/env python3
"""CycloneDX SBOM 生成器（软件供应链安全 P1#2 落地）。

从全量哈希锁 requirements-lock.txt（``name==version --hash=sha256:...``）生成
CycloneDX 1.5 JSON SBOM。portable-release.yml 在构建会话内调用并把产物随资产
分发，使安装器/便携包制品的 Python 依赖成分可机读、可按漏洞定位影响面。

诚实边界：
- 覆盖 Python 依赖侧；desktop npm 树由 package-lock 覆盖、GHCR 镜像由
  docker-publish.yml 的 buildx sbom/provenance attestation 覆盖（各自通道已有）；
- SBOM 生成自锁文件，不含未入锁的运行期动态依赖（本仓无运行期装包路径）；
- 许可证字段留空（锁文件不含许可信息），许可台账见 docs/LICENSE_COMPLIANCE.md。

用法：
    python scripts/generate_sbom.py --lock requirements-lock.txt --output sbom-python.cdx.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tomllib
import uuid
from datetime import UTC, datetime
from pathlib import Path

TOOL_NAME = "seedvr2-generate-sbom"
TOOL_VERSION = "1.0.0"
#: serialNumber 稳定命名空间：同内容锁文件 → 同 serial（便于幂等核对）
_NS_URL = "https://github.com/ReSerendipity/SeedVR2-lite/sbom"

_PIN_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s]+)((?:\s+--hash=[A-Za-z0-9-]+:[A-Za-z0-9]+)*)\s*$")
_HASH_RE = re.compile(r"--hash=([A-Za-z0-9-]+):([A-Za-z0-9]+)")
_ALG_MAP = {"sha256": "SHA-256", "sha512": "SHA-512", "sha384": "SHA-384"}


def _join_continuations(text: str) -> list[str]:
    """把 pip 哈希锁的续行（行尾反斜杠）合并为逻辑行，保留注释/空行原样。"""
    logical: list[str] = []
    buf = ""
    for raw in text.splitlines():
        line = raw.strip()
        if line.endswith("\\"):
            buf += line[:-1] + " "
            continue
        buf += line
        if buf.strip():
            logical.append(buf.strip())
        buf = ""
    if buf.strip():
        logical.append(buf.strip())
    return logical


def parse_lock(text: str) -> list[dict]:
    """解析哈希锁文本为 CycloneDX components（按 name 排序、hash 保序去重）。

    支持两种排版：单行（``name==ver --hash=...``）与 pip 续行（行尾 ``\\``）。
    注释与空行跳过。

    Raises:
        ValueError: 存在既非注释/空行又不符合钉版格式的逻辑行。
    """
    components: list[dict] = []
    for line in _join_continuations(text):
        if line.startswith("#"):
            continue
        m = _PIN_RE.match(line)
        if not m:
            raise ValueError(f"unparsable lock line: {line[:80]}")
        name, version, hashes_part = m.group(1), m.group(2), m.group(3)
        hashes: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for alg, content in _HASH_RE.findall(hashes_part):
            key = (alg.lower(), content.lower())
            if key in seen:
                continue
            seen.add(key)
            alg_name = _ALG_MAP.get(alg.lower())
            if alg_name:
                hashes.append({"alg": alg_name, "content": content})
        comp: dict = {
            "type": "library",
            "bom-ref": f"pkg:pypi/{name.lower()}@{version}",
            "name": name,
            "version": version,
            "purl": f"pkg:pypi/{name.lower()}@{version}",
        }
        if hashes:
            comp["hashes"] = hashes
        components.append(comp)
    components.sort(key=lambda c: (c["name"].lower(), c["version"]))
    return components


def _default_component_version() -> str:
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    with pyproject.open("rb") as f:
        return str(tomllib.load(f)["project"]["version"])


def build_bom(
    lock_text: str,
    timestamp: str,
    component_version: str,
) -> dict:
    """构造完整 CycloneDX 文档（serialNumber 由锁内容决定，幂等）。"""
    components = parse_lock(lock_text)
    if not components:
        raise ValueError("lock produced zero components")
    digest = hashlib.sha256(lock_text.encode("utf-8")).hexdigest()
    serial = uuid.uuid5(uuid.NAMESPACE_URL, f"{_NS_URL}/{digest}")
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{serial}",
        "version": 1,
        "metadata": {
            "timestamp": timestamp,
            "tools": {"components": [{"type": "application", "name": TOOL_NAME, "version": TOOL_VERSION}]},
            "component": {
                "bom-ref": "seedvr2-lite",
                "type": "application",
                "name": "seedvr2-lite",
                "version": component_version,
            },
        },
        "components": components,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate CycloneDX SBOM from a hash-locked requirements file")
    parser.add_argument("--lock", default="requirements-lock.txt", help="hash-locked requirements file")
    parser.add_argument("--output", required=True, help="output JSON path")
    parser.add_argument(
        "--timestamp", default=None, help="override metadata.timestamp (ISO8601, for reproducible tests)"
    )
    parser.add_argument(
        "--component-version", default=None, help="top component version (default: read pyproject.toml)"
    )
    args = parser.parse_args(argv)

    lock_path = Path(args.lock)
    lock_text = lock_path.read_text(encoding="utf-8")
    ts = args.timestamp or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    version = args.component_version or _default_component_version()
    bom = build_bom(lock_text, ts, version)
    Path(args.output).write_text(json.dumps(bom, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[SBOM] {len(bom['components'])} components -> {args.output} (serial {bom['serialNumber']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
