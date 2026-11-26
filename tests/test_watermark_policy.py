#!/usr/bin/env python3
"""水印失败处置策略测试（评估报告 R2：fail-closed 兜底 + 落盘后复验）。

覆盖：
- resolve_watermark_failure_policy：默认值 / 合法值 / 非法值回退
- embed_with_retry：成功路径 / 首次失败重试成功 / 双重失败返回错误
- handle_watermark_failure：block 抛异常 + 审计 / mark_metadata 降级审计 / ignore 静默
- write_provenance_sidecar：侧车文件内容与路径规则
- select_image_embed_tier：有损格式必须走鲁棒档（2026-09-19 实测无损档经 JPEG q95 即失效）
- output_carries_watermark + handle_watermark_loss：落盘复验与三档处置
- report_missing_watermark_key：密钥缺失只审计一次

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

import json
from pathlib import Path

import numpy as np
import pytest

from app.integrated_app.security.watermark import (
    _VIDEO_ALPHA,
    _VIDEO_REPEAT,
    _WATERMARK_ALPHA,
    embed_watermark,
)
from app.integrated_app.services import watermark_policy
from app.integrated_app.services.watermark_policy import (
    DEFAULT_WATERMARK_FAILURE_POLICY,
    WatermarkEmbedError,
    embed_with_retry,
    handle_watermark_failure,
    handle_watermark_loss,
    output_carries_watermark,
    resolve_watermark_failure_policy,
    select_image_embed_tier,
    write_provenance_sidecar,
)


def _img() -> np.ndarray:
    rng = np.random.default_rng(42)
    return rng.integers(0, 255, size=(64, 64, 3), dtype=np.uint8)


# ---------- resolve_watermark_failure_policy ----------


def test_resolve_default_when_unconfigured():
    assert resolve_watermark_failure_policy({}) == DEFAULT_WATERMARK_FAILURE_POLICY


@pytest.mark.parametrize("policy", ["mark_metadata", "block", "ignore"])
def test_resolve_valid_values(policy):
    config = {"runtime": {"security": {"watermark_on_failure": policy}}}
    assert resolve_watermark_failure_policy(config) == policy


def test_resolve_invalid_falls_back_to_default():
    config = {"runtime": {"security": {"watermark_on_failure": "explode"}}}
    assert resolve_watermark_failure_policy(config) == DEFAULT_WATERMARK_FAILURE_POLICY


# ---------- embed_with_retry ----------


def test_embed_success(monkeypatch):
    import app.integrated_app.security.watermark as wm

    monkeypatch.setattr(wm, "embed_watermark", lambda img, payload=None: img + 1)
    out, embedded, err = embed_with_retry(_img(), payload="task-1")
    assert embedded is True
    assert err is None


def test_embed_retry_then_success(monkeypatch):
    import app.integrated_app.security.watermark as wm

    calls = {"n": 0}

    def flaky(img, payload=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("transient")
        return img

    monkeypatch.setattr(wm, "embed_watermark", flaky)
    out, embedded, err = embed_with_retry(_img())
    assert embedded is True and err is None and calls["n"] == 2


def test_embed_double_failure_returns_original(monkeypatch):
    import app.integrated_app.security.watermark as wm

    def broken(img, payload=None):
        raise ValueError("deterministic")

    monkeypatch.setattr(wm, "embed_watermark", broken)
    src = _img()
    out, embedded, err = embed_with_retry(src)
    assert embedded is False
    assert err is not None and "deterministic" in err
    assert np.array_equal(out, src)


# ---------- handle_watermark_failure ----------


def test_handle_block_raises_and_audits(monkeypatch):
    events = []
    monkeypatch.setattr(watermark_policy, "audit_event", lambda event, **kw: events.append((event, kw)))
    with pytest.raises(WatermarkEmbedError, match="block"):
        handle_watermark_failure(policy="block", error="boom", payload="task-9")
    assert events and events[0][0] == "WATERMARK_EMBED_BLOCKED"


def test_handle_mark_metadata_audits(monkeypatch, caplog):
    events = []
    monkeypatch.setattr(watermark_policy, "audit_event", lambda event, **kw: events.append((event, kw)))
    handle_watermark_failure(policy="mark_metadata", error="boom", payload="task-9")
    assert events and events[0][0] == "WATERMARK_EMBED_DEGRADED"
    assert any("mark_metadata" in r.message for r in caplog.records)


def test_handle_ignore_is_silent(monkeypatch):
    events = []
    monkeypatch.setattr(watermark_policy, "audit_event", lambda event, **kw: events.append((event, kw)))
    handle_watermark_failure(policy="ignore", error="boom")
    assert events == []


# ---------- write_provenance_sidecar ----------


def test_sidecar_written_to_provenance_dir(tmp_path, monkeypatch):
    """溯源记录落在 data/provenance（此处经 env 注入到 tmp），文件名含产物路径哈希。"""
    monkeypatch.setenv("SEEDVR2_PROVENANCE_DIR", str(tmp_path / "prov"))
    output = tmp_path / "output.png"
    output.write_bytes(b"\x89PNG fake")
    sidecar = write_provenance_sidecar(str(output), payload="task-1")
    assert Path(sidecar).parent == tmp_path / "prov"
    assert Path(sidecar).name.startswith("output__") and Path(sidecar).name.endswith(".provenance.json")
    body = json.loads(Path(sidecar).read_text(encoding="utf-8"))
    assert body["output_path"] == str(output.resolve())
    assert body["watermark_embedded"] is False
    assert body["payload"] == "task-1"
    assert body["tool"] == "SeedVR2"


# ---------- select_image_embed_tier / output_carries_watermark（自适应档位 + 落盘复验） ----------


def _smooth_img(size: int = 512) -> np.ndarray:
    """平滑渐变图（低频内容）：鲁棒档经 JPEG 的存活对内容敏感，均匀白噪声是
    中频 QIM 的最坏情形（实测连 q95 都活不下来），拿它当夹具会测出假阴性。

    尺寸取 512：签名载荷（task_id + 64 位摘要）× repeat 3 需 ≥2328 个 8x8 块，
    约 400px 以下冗余会被砍（见 test_repeat_downgrade_is_warned）。
    """
    ramp = np.linspace(40, 220, size, dtype=np.uint8)
    return np.repeat(ramp[None, :, None], size, axis=0)[:, :, [0, 0, 0]].copy()


@pytest.mark.parametrize("ext", [".jpg", ".jpeg", ".webp", ".JPEG"])
def test_lossy_formats_select_robust_tier(ext):
    assert select_image_embed_tier(ext) == (_VIDEO_ALPHA, _VIDEO_REPEAT)


@pytest.mark.parametrize("ext", [".png", ".bmp", ".tiff", ""])
def test_lossless_formats_select_lossless_tier(ext):
    assert select_image_embed_tier(ext) == (_WATERMARK_ALPHA, 1)


def _save_jpeg(arr: np.ndarray, path: Path, quality: int = 95) -> None:
    from PIL import Image

    # 与图像管线的 JPEG 落盘参数一致（quality=95 + optimize）
    Image.fromarray(arr).save(path, quality=quality, optimize=True)


def test_robust_tier_survives_pipeline_jpeg_encoder(tmp_path):
    """有损格式换鲁棒档后，产物经流水线同款编码仍可验签（此前该场景水印全灭）。"""
    alpha, repeat = select_image_embed_tier(".jpg")
    path = tmp_path / "out.jpg"
    _save_jpeg(embed_watermark(_smooth_img(), payload="task-jpg", alpha=alpha, repeat=repeat), path)
    assert output_carries_watermark(str(path)) is True


def test_production_shaped_payload_keeps_redundancy_at_512(tmp_path, monkeypatch):
    """生产载荷形状（品牌前缀 + 16 位任务 ID + 64 位摘要 ≈ 824 bit）在 512×512 上
    仍撑得起 repeat=3，且经流水线同款编码后可验签——容量预算的回归锁。"""
    monkeypatch.setenv("SEEDVR2_WATERMARK_KEY", "issuer-key-production")
    alpha, repeat = select_image_embed_tier(".jpg")
    path = tmp_path / "prod.jpg"
    _save_jpeg(embed_watermark(_smooth_img(), payload="3f9a1c7e2b8d40a6", alpha=alpha, repeat=repeat), path)
    assert output_carries_watermark(str(path)) is True


def test_lossless_tier_is_erased_by_jpeg_encoder(tmp_path):
    """回归哨兵：无损档水印经 JPEG q95 必失——这正是有损格式必须换档的实测依据。

    反过来不成立：鲁棒档对 JPEG 是临界存活（依内容摆动），所以产物真伪以
    落盘复验为准，不靠档位承诺。
    """
    alpha, repeat = select_image_embed_tier(".png")
    path = tmp_path / "out.jpg"
    _save_jpeg(embed_watermark(_smooth_img(), payload="task-jpg", alpha=alpha, repeat=repeat), path)
    assert output_carries_watermark(str(path)) is False


def test_repeat_downgrade_is_warned(tmp_path, monkeypatch, caplog):
    """256×256 装不下「签名载荷 × repeat 3」，冗余被砍必须告警而非静默。"""
    import logging

    monkeypatch.setenv("SEEDVR2_WATERMARK_KEY", "fixed-key-for-deterministic-payload-length")
    alpha, repeat = select_image_embed_tier(".jpg")
    small = np.full((256, 256, 3), 128, dtype=np.uint8)
    with caplog.at_level(logging.WARNING, logger="app.integrated_app.security.watermark"):
        embed_watermark(small, payload="task-small", alpha=alpha, repeat=repeat)
    assert any("重复码" in m for m in caplog.messages), "冗余降档不得静默发生"


def test_lossless_tier_survives_png_output(tmp_path):
    from PIL import Image

    alpha, repeat = select_image_embed_tier(".png")
    path = tmp_path / "out.png"
    Image.fromarray(embed_watermark(_smooth_img(), payload="task-png", alpha=alpha, repeat=repeat)).save(path)
    assert output_carries_watermark(str(path)) is True


def test_output_carries_watermark_false_for_unreadable_file(tmp_path):
    """读不到/解不开一律判「未携带」，让调用方走策略处置而非当作有水印。"""
    assert output_carries_watermark(str(tmp_path / "missing.png")) is False


# ---------- handle_watermark_loss（落盘后水印丢失） ----------


def test_loss_mark_metadata_writes_sidecar(tmp_path, monkeypatch):
    monkeypatch.setenv("SEEDVR2_PROVENANCE_DIR", str(tmp_path / "prov"))
    events = []
    monkeypatch.setattr(watermark_policy, "audit_event", lambda event, **kw: events.append(event))
    output = tmp_path / "o.png"
    output.write_bytes(b"\x89PNG fake")
    handle_watermark_loss(policy="mark_metadata", output_path=str(output), error="编码吃掉水印", payload="task-1")
    assert output.exists(), "mark_metadata 档不得删除产物"
    records = list((tmp_path / "prov").glob("o__*.provenance.json"))
    assert len(records) == 1, "溯源记录应落在注入目录，而非产物旁边"
    sidecar = json.loads(records[0].read_text(encoding="utf-8"))
    assert sidecar["reason"] == "编码吃掉水印"
    assert events == ["WATERMARK_LOSS_DEGRADED"]


def test_loss_block_removes_output_and_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("SEEDVR2_PROVENANCE_DIR", str(tmp_path / "prov"))
    events = []
    monkeypatch.setattr(watermark_policy, "audit_event", lambda event, **kw: events.append(event))
    output = tmp_path / "o.png"
    output.write_bytes(b"\x89PNG fake")
    with pytest.raises(WatermarkEmbedError, match="落盘后不可验证"):
        handle_watermark_loss(policy="block", output_path=str(output), error="编码吃掉水印")
    assert not output.exists(), "block 档必须兑现「产出不落盘」"
    assert not list((tmp_path / "prov").glob("*.provenance.json")) if (tmp_path / "prov").exists() else True
    assert events == ["WATERMARK_LOSS_BLOCKED"]


def test_loss_ignore_keeps_output_without_sidecar(tmp_path, monkeypatch):
    monkeypatch.setenv("SEEDVR2_PROVENANCE_DIR", str(tmp_path / "prov"))
    events = []
    monkeypatch.setattr(watermark_policy, "audit_event", lambda event, **kw: events.append(event))
    output = tmp_path / "o.png"
    output.write_bytes(b"\x89PNG fake")
    handle_watermark_loss(policy="ignore", output_path=str(output), error="编码吃掉水印")
    assert output.exists()
    assert not list((tmp_path / "prov").glob("*.provenance.json")) if (tmp_path / "prov").exists() else True
    assert events == []


# ---------- report_missing_watermark_key（密钥缺失降级可见化） ----------


def test_missing_key_audited_once(monkeypatch):
    import app.integrated_app.security.watermark as wm

    events = []
    monkeypatch.setattr(watermark_policy, "audit_event", lambda event, **kw: events.append(event))
    monkeypatch.setattr(watermark_policy, "_key_missing_audited", False)
    monkeypatch.setattr(wm, "watermark_key_available", lambda: False)

    assert watermark_policy.report_missing_watermark_key() is False
    assert watermark_policy.report_missing_watermark_key() is False
    assert events == ["WATERMARK_KEY_MISSING"], "取证日志只记一次，避免批量/逐帧刷屏"


def test_key_present_audits_nothing(monkeypatch):
    import app.integrated_app.security.watermark as wm

    events = []
    monkeypatch.setattr(watermark_policy, "audit_event", lambda event, **kw: events.append(event))
    monkeypatch.setattr(wm, "watermark_key_available", lambda: True)

    assert watermark_policy.report_missing_watermark_key() is True
    assert events == []


def test_invalid_key_file_backed_up_and_audited(monkeypatch, tmp_path):
    """0 字节 / 过短密钥文件：改名留证 + 审计 WATERMARK_KEY_INVALID，再走内核重建。"""
    import app.integrated_app.security.watermark as wm

    key_path = tmp_path / ".watermark_key"
    key_path.write_text("", encoding="utf-8")
    events = []
    monkeypatch.setattr(watermark_policy, "audit_event", lambda event, **kw: events.append(event))
    monkeypatch.setattr(watermark_policy, "_WATERMARK_KEY_PATH", key_path)
    monkeypatch.setattr(wm, "watermark_key_available", lambda: False)

    assert watermark_policy.ensure_watermark_key_file() is False
    backups = list(tmp_path.glob(".watermark_key.invalid-*"))
    assert len(backups) == 1, "损坏文件必须改名留证，不能直接覆盖删除"
    assert events == ["WATERMARK_KEY_INVALID"]
    assert not key_path.exists(), "损坏文件应已移走，交给内核重建"


def test_invalid_utf8_key_is_backed_up_without_crashing(monkeypatch, tmp_path):
    import app.integrated_app.security.watermark as wm

    key_path = tmp_path / ".watermark_key"
    key_path.write_bytes(b"\xff\xfe\x00")
    events = []
    monkeypatch.setattr(watermark_policy, "audit_event", lambda event, **kw: events.append((event, kw["detail"])))
    monkeypatch.setattr(watermark_policy, "_WATERMARK_KEY_PATH", key_path)
    monkeypatch.setattr(wm, "watermark_key_available", lambda: False)

    assert watermark_policy.ensure_watermark_key_file() is False
    assert len(list(tmp_path.glob(".watermark_key.invalid-*"))) == 1
    assert events[0][0] == "WATERMARK_KEY_INVALID"
    assert "已备份" in events[0][1] and "重建失败" in events[0][1]


def test_invalid_key_audit_records_backup_failure(monkeypatch, tmp_path):
    import app.integrated_app.security.watermark as wm

    key_path = tmp_path / ".watermark_key"
    key_path.write_text("short", encoding="utf-8")
    events = []
    monkeypatch.setattr(watermark_policy, "audit_event", lambda event, **kw: events.append((event, kw["detail"])))
    monkeypatch.setattr(watermark_policy, "_WATERMARK_KEY_PATH", key_path)
    monkeypatch.setattr(wm, "watermark_key_available", lambda: False)
    monkeypatch.setattr(Path, "replace", lambda self, target: (_ for _ in ()).throw(OSError("readonly")))

    assert watermark_policy.ensure_watermark_key_file() is False
    assert "备份失败" in events[0][1]
    assert key_path.exists()


def test_short_key_backup_remains_usable_for_verification(monkeypatch, tmp_path):
    import app.integrated_app.security.watermark as wm

    key_path = tmp_path / ".watermark_key"
    key_path.write_text("short", encoding="utf-8")
    monkeypatch.setattr(watermark_policy, "_WATERMARK_KEY_PATH", key_path)
    monkeypatch.setattr(wm, "_WATERMARK_KEY_FILE_DATA", key_path)
    monkeypatch.setattr(wm, "_WATERMARK_KEY_FILE_LEGACY", tmp_path / "legacy-key")
    monkeypatch.setattr(wm, "_WATERMARK_KEY_FILE_OLD", tmp_path / "old-key")
    monkeypatch.delenv(wm._WATERMARK_KEY_ENV, raising=False)

    assert watermark_policy.ensure_watermark_key_file() is True
    backup = next(tmp_path.glob(".watermark_key.invalid-*"))
    assert backup.read_text(encoding="utf-8").strip() == "short"
    assert b"short" in wm._load_verify_keys()
    signed = wm._sign_payload("historical-payload", b"short")
    assert wm._verify_signature(signed, b"short") == "historical-payload"
