"""Version consistency basic test (family simplified).

Verify:
- Version number follows SemVer (x.y.z)
- No hardcoded old version (0.1.0 / 0.0.1)
"""

from __future__ import annotations

import json
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _find_version_file():
    candidates = [
        "package.json",
        "pyproject.toml",
        "version.json",
        "gradle.properties",
    ]
    for c in candidates:
        if (PROJECT_ROOT / c).exists():
            return c
    return None


def test_version_file_exists():
    assert _find_version_file() is not None


def test_version_is_semver():
    vfile = _find_version_file()
    assert vfile

    content = (PROJECT_ROOT / vfile).read_text(encoding="utf-8", errors="ignore")

    if vfile == "package.json":
        data = json.loads(content)
        version = data.get("version", "")
    elif vfile == "pyproject.toml":
        m = re.search(r'version\s*=\s*["\']([^"\']+)["\']', content)
        version = m.group(1) if m else ""
    elif vfile == "version.json":
        data = json.loads(content)
        version = data.get("version", "")
    elif vfile == "gradle.properties":
        m = re.search(r"versionName\s*=\s*([^\n]+)", content)
        version = m.group(1).strip() if m else ""
    else:
        version = ""

    assert re.fullmatch(r"\d+\.\d+\.\d+", version)


def test_no_hardcoded_old_version():
    old_patterns = [r'version\s*=\s*["\']0\.1\.0["\']', r'version\s*=\s*["\']0\.0\.1["\']']

    offenders = []
    for pyfile in PROJECT_ROOT.rglob("*.py"):
        # dist/ 与 build/ 是构建产物根（二者均在 .gitignore 中）：桌面发布会在
        # dist/tauri-release/staging 下解压便携 runtime，第三方 site-packages 里
        # 满是本测试要抓的那类历史版本号赋值字面量，但不属于本项目源码。
        if any(skip in str(pyfile) for skip in [".venv", "__pycache__", "node_modules", "dist", "build"]):
            continue
        try:
            content = pyfile.read_text(encoding="utf-8", errors="ignore")
            for pat in old_patterns:
                if re.search(pat, content):
                    offenders.append(str(pyfile.relative_to(PROJECT_ROOT)))
                    break
        except Exception:
            pass

    assert offenders == []
