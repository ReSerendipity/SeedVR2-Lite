#!/usr/bin/env python3
"""引用可用性检查：追踪文件不得把读者指向「未随仓库分发」的本地文件而无可获取性说明。

背景（2026-09-18）：`AGENTS.md` / `CONTRIBUTING.md` / `docs/agents/**` / `precheck.ps1` 等
治理文档按「干净交付」决策被 `.gitignore` 排除，但**追踪态**文件仍以规范口吻指向它们——
新克隆与 CI 消费者打开的是不存在的文件（幻影引用）。`scripts/check_spec_refs.py` 只检查
「引用的可执行路径是否存在于本机」，方向相反，覆盖不到这一类，故独立成门禁。

口径：
- 扫描对象：追踪文件中的文本文件（`SCAN_SUFFIX`）
- 命中条件：行内出现路径样 token，且该路径在本机存在、**未被 git 追踪**、**被 .gitignore 排除**
  （= 维护者本地文件，克隆里没有）
- 放行条件（任一即可）：
  1. 同行或上下 2 行内含可获取性说明（`MARKERS`）
  2. 文件内有一行「本地未分发引用：…」声明，且引用命中其列出的路径或前缀
  3. 命中 `docs/ci/local_ref_baseline.json` 历史基线（只防新增，不追历史）
- 运行：`python scripts/check_local_only_refs.py`      暂存区（pre-commit 用）
        `python scripts/check_local_only_refs.py --all`  全库（CI / 人工审计用）
- 规则详见 docs/CODING_STANDARDS.md「5. 禁区与门禁口径（公开子集）」
"""

import argparse
import contextlib
import json
import re
import subprocess  # nosec B404（仅以参数列表调用 git，无 shell=True）
import sys
from pathlib import Path

# 报告文本含中文与 ⚠ 等字符：Windows 控制台默认 GBK，违例一打印就 UnicodeEncodeError，
# 门禁于是拿栈回溯顶替可读报告（仍然非零退出，但人看不到该修什么）。
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        with contextlib.suppress(OSError, ValueError):
            _stream.reconfigure(encoding="utf-8", errors="replace")

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
BASELINE_PATH = ROOT / "docs" / "ci" / "local_ref_baseline.json"

# 本脚本自身与「定义忽略规则的文件」不参与扫描：.gitignore / .gitattributes 的内容就是排除清单，
# 不是写给读者的文件指针（将它们计入只会把规则表本身刷成一片假阳性）。
SELF_EXEMPT = {"scripts/check_local_only_refs.py", ".gitignore", ".gitattributes", "docs/ci/local_ref_baseline.json"}

# 被扫描的文本文件类型
SCAN_SUFFIX = {".md", ".py", ".ps1", ".bat", ".sh", ".yml", ".yaml", ".toml", ".js", ".ts", ".txt"}
# 可能被当作「读者请去查阅」的目标类型（比 SCAN 多出的窄集合：代码/数据/配置样例）
REF_SUFFIX = {
    ".md",
    ".py",
    ".ps1",
    ".bat",
    ".sh",
    ".yml",
    ".yaml",
    ".toml",
    ".js",
    ".ts",
    ".txt",
    ".json",
    ".rs",
    ".csv",
}
# 运行/构建产物目录：出现在文档里是路径约定而非「请去阅读的文件」，不属本门禁口径
ARTIFACT_PARTS = {
    "node_modules",
    "dist",
    "build",
    "outputs",
    "logs",
    "backups",
    "screenshots",
    "uploads",
    "checkpoints",
    "heartbeats",
    "provenance",  # 空目录时 `ls-files --others --ignored` 列不到，.gitignore 豁免失效，只能靠本表
    "target",
    "gen",
    "model",
    "playwright-report",
    "test-results",
    "__pycache__",
    ".venv",
    "venv",
    ".uv-cache",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".hypothesis",
    ".qoder",
    ".trae",
    "_archive",
    "_rendered",
    ".spec_audit",
}

TOKEN_RE = re.compile(
    r"(?<![\w./-])"
    r"((?:[\w.\-]+/)*[\w.\-]+\.(?:"
    + "|".join(sorted(s.lstrip(".") for s in REF_SUFFIX))
    + r")|(?:[\w.\-]+/){1,6}[\w.\-]+/)"
)
# 行内 / 邻近行的可获取性说明关键词
MARKERS = (
    "未随仓库分发",
    "不随仓库分发",
    "不入库",
    "未入库",
    "不入远程",
    "本地文件",
    "本地文档",
    "维护者本地",
    "maintainer-local",
    "仅本地",
    "本地保留",
    "未分发",
    "未发布",
    "克隆里没有",
    "克隆后不存在",
    "被 .gitignore 忽略",
    "local-only",
    "not distributed",
    "not shipped",
)
# 文件级声明行的锚点关键词
DECLARATION_ANCHOR = "本地未分发引用"
CONTEXT_LINES = 2

# 「本机独有资产」名单：这些名字被 .gitignore 以裸文件名排除（无斜线，任意深度命中）。
# 原有判据的 ignored 集合取自 `git ls-files --others --ignored`，它只列本机磁盘上真实存在
# 的路径——于是目标在本机不存在时（恰好就是 CI 与全新克隆的情形）这条引用查不出来，门禁
# 会绿着放过它自己要防的那类幻影引用。本名单不依赖磁盘存在性，本机与 CI 两侧口径一致。
# 新增条目必须先确认它在 .gitignore 里确是裸文件名排除（tests/test_local_ref_gate.py 有对照用例）。
LOCAL_ONLY_NAMES = frozenset(
    {
        "AGENTS.md",
        "CONTRIBUTING.md",
        "LOCAL_RULES.md",
        "FIX_LOG.md",
        "precheck.ps1",
    }
)


def _git_lines(*args):
    """跑 git 并按 NUL 切分（-z 关掉 core.quotepath，避免中文路径被八进制转义）。"""
    out = subprocess.run(  # nosec B603, B607（git 只读命令）
        ["git", "-C", str(ROOT), *args],
        capture_output=True,
        check=True,
    )
    return [p for p in out.stdout.decode("utf-8", "replace").split("\0") if p]


def tracked_files():
    return set(_git_lines("ls-files", "-z"))


def ignored_paths():
    """被 .gitignore 排除的未追踪路径（目录以 `dir/` 形式坍缩，便于前缀判断）。"""
    return {
        p.rstrip("/")
        for p in _git_lines("ls-files", "--others", "--ignored", "--exclude-standard", "--directory", "-z")
    }


def read_lines(path):
    raw = path.read_bytes()
    if b"\x00" in raw[:4096]:
        return None  # 二进制
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return raw.decode(enc).splitlines()
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace").splitlines()


def resolve_candidates(file_rel, token):
    """token 可能是仓库根相对路径，也可能是同文档目录相对路径；只保留磁盘上真实存在的。"""
    tok = token.rstrip("/")
    parent = Path(file_rel).parent.as_posix()
    cands = {tok}
    if parent != ".":
        cands.add(f"{parent}/{tok}")
    return sorted(c for c in cands if (ROOT / c).is_file() or (ROOT / c).is_dir())


def is_artifact(rel):
    return bool(set(rel.split("/")) & ARTIFACT_PARTS)


def declared_refs(lines):
    """收集文件级声明：「本地未分发引用：A、B/」→ 这些路径及其子路径在本文件内放行。

    声明常常换行书写（如 CHANGELOG 顶部的 blockquote），因此从锚点行起一直读到空行为止。
    """
    declared = set()
    for idx, line in enumerate(lines):
        if DECLARATION_ANCHOR not in line:
            continue
        block = [line]
        for nxt in lines[idx + 1 : idx + 6]:
            if not nxt.strip():
                break
            block.append(nxt)
        for text in block:
            for tok in TOKEN_RE.findall(text):
                declared.add(tok.rstrip("*/"))
            # 通配写法（docs/agents/**、examples/）去掉通配后按前缀放行
            for seg in re.split(r"[、,，;；]", text):
                raw = seg.strip().strip("`*«»()（） ")
                if "/" in raw or raw.endswith("*") or raw.endswith("/"):
                    declared.add(raw.rstrip("*").rstrip("/"))
    return {d for d in declared if d and " " not in d}


def _norm_rel(cand: str) -> str:
    """统一成仓根相对形式，保证本机与干净检出两种环境下回报的 ref 完全一致。"""
    return cand[2:] if cand.startswith("./") else cand


def hits_local_only(rel, token, tracked, ignored):
    """token 是否指向「本机有、克隆没有」的未分发文件。

    同名 token 可能有多种解析（仓根相对 / 同文档目录相对）：只要其中任一种能解析到
    已跟踪文件，就当它是合法引用（避免把「根 `README.md`」误判成 docs/README.md）。
    """
    if is_artifact(token):
        return None
    cands = resolve_candidates(rel, token)
    if cands and any(c in tracked for c in cands):
        return None

    # 本机独有名单兜底：名单里的名字被 .gitignore 以裸文件名排除，在任何环境都不随仓库
    # 分发。而上面的 resolve_candidates 只保留磁盘上存在的路径、ignored 也只列本机磁盘上
    # 存在的忽略项——目标在本机不存在时（CI 与全新克隆正是这种状态）整条引用会被无声放过，
    # 门禁只在维护者本机有效。此判据不依赖磁盘存在性，两侧口径一致。
    tok = _norm_rel(token.rstrip("/"))
    bare = tok.split("/")[-1]
    if bare in LOCAL_ONLY_NAMES:
        for cand in cands:
            if cand not in tracked and not is_artifact(cand):
                return _norm_rel(cand)
        return None if cands else tok

    for cand in cands:
        if is_artifact(cand):
            continue
        parts = cand.split("/")
        for i in range(1, len(parts) + 1):
            if "/".join(parts[:i]) in ignored:
                return cand
    return None


def load_baseline():
    if not BASELINE_PATH.is_file():
        return {}
    data = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    entries = data.get("entries", data if isinstance(data, list) else [])
    return {(e["file"], e["ref"]): e.get("reason", "") for e in entries}


def scan(files, tracked, ignored, baseline):
    violations = []
    used_baseline = set()
    for rel in sorted(files):
        path = ROOT / rel
        if rel in SELF_EXEMPT or not path.is_file():
            continue
        lines = read_lines(path)
        if lines is None:
            continue
        # 无扩展名的脚本（如 .githooks/pre-push）同样扫描：首行是 # 注释/shebang 即视为文本
        if path.suffix.lower() not in SCAN_SUFFIX and not (lines and lines[0].startswith("#")):
            continue
        declared = declared_refs(lines)
        for idx, line in enumerate(lines, 1):
            for token in sorted(set(TOKEN_RE.findall(line))):
                bad = hits_local_only(rel, token, tracked, ignored)
                if bad is None:
                    continue
                window = " ".join(lines[max(0, idx - CONTEXT_LINES) : idx + CONTEXT_LINES])
                if any(m in window for m in MARKERS):
                    continue
                if any(bad == d or bad.startswith(d + "/") or d.startswith(bad + "/") for d in declared):
                    continue
                key = (rel, bad)
                if key in baseline:
                    used_baseline.add(key)
                    continue
                violations.append((rel, idx, bad, token, line.strip()[:100]))
    stale = sorted(set(baseline) - used_baseline)
    return violations, stale


def main(argv=None):
    parser = argparse.ArgumentParser(description="追踪文件引用未分发本地文件（幻影引用）检查")
    parser.add_argument("--all", action="store_true", help="全库扫描（默认只扫暂存区）")
    parser.add_argument("--json", type=Path, default=None, help="把结果写入指定 JSON（供 CI 归档）")
    args = parser.parse_args(argv)

    tracked = tracked_files()
    ignored = ignored_paths()
    baseline = load_baseline()
    if args.all:
        files = tracked
    else:
        files = {f for f in _git_lines("diff", "--cached", "--name-only", "--diff-filter=ACM", "-z") if f in tracked}

    violations, stale = scan(files, tracked, ignored, baseline)

    report = {
        "scanned_files": len(files),
        "violations": [
            {"file": f, "line": ln, "ref": ref, "token": tok, "text": txt} for f, ln, ref, tok, txt in violations
        ],
        "stale_baseline": [{"file": f, "ref": ref} for f, ref in stale],
    }
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if violations:
        print(f"[local-refs] 发现 {len(violations)} 处幻影引用（追踪文件指向未随仓库分发的本地文件）：")
        for f, ln, ref, _tok, txt in violations:
            print(f"  {f}:{ln} -> {ref} | {txt}")
        print("\n修复方式（任选其一，口径见 docs/CODING_STANDARDS.md 第 5 节）：")
        print("  1) 改指向已分发的路径（如 docs/CODING_STANDARDS.md 公开子集）")
        print("  2) 就地补可获取性说明：未随仓库分发 / 维护者本地文件 / 克隆后不存在 …")
        print(f"  3) 文件顶部汇总：「> {DECLARATION_ANCHOR}：a.md、dir/」（本文件内统一声明）")
        print(f"  4) 确属历史记录则登记进 {BASELINE_PATH.relative_to(ROOT).as_posix()}（需说明理由，不追历史只防新增）")
        rc = 1
    else:
        print(f"[local-refs] 通过：{len(files)} 个追踪文件，无新增幻影引用")
        rc = 0
    if stale:
        print(f"[local-refs] 提示：基线中 {len(stale)} 条已不再命中（可清理）")
        for f, ref in stale:
            print(f"  stale  {f} -> {ref}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
