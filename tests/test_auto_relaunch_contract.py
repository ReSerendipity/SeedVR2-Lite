"""自更新第 7 步契约：Rust 传 /RELAUNCH ↔ NSIS 检测 ↔ 前端提示，三处成对存在。"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8-sig", errors="replace")


def test_rust_passes_relaunch_flag():
    src = _read("desktop/src-tauri/src/updater.rs")
    assert '"/RELAUNCH"' in src, "更新器未向安装器传 /RELAUNCH 标志"
    assert '"/S"' in src, "更新器未传 /S 静默标志"
    assert "std::process::exit(0)" in src, "更新器未有序退出"


def test_nsis_detects_relaunch_and_execs_app():
    nsi = _read("scripts/installer/setup.nsi")
    assert '${StrStr} $0 "$CMDLINE" "/RELAUNCH"' in nsi, "NSIS 未检测 /RELAUNCH 标志"
    assert r"""Exec '"$INSTDIR\SeedVR2.exe"'""" in nsi, "NSIS 未在装完后拉起主程序（第 7 步自动重启缺失）"


def test_frontend_announces_auto_restart():
    js = _read("desktop/src/desktop-bridge.js")
    assert 'listen("update-installing"' in js, "前端未监听 update-installing"
    assert "自动完成更新并重新启动" in js, "前端提示未说明会自动重启"


def test_ci_verify_workflow_exists():
    wf = ROOT / ".github" / "workflows" / "verify-relaunch.yml"
    assert wf.exists(), "缺 CI 端到端验收 workflow"
    text = wf.read_text(encoding="utf-8")
    assert "/RELAUNCH" in text, "验收 workflow 未带 /RELAUNCH 参数"
