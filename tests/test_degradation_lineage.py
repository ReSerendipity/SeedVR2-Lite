"""OOM 降级血缘测试（数据治理 P0-3）。

验收标准（对应评估报告 §9.2 P0-3）：
1. 未降级时 build_degradation_metadata 返回 None（不影响正常记录）；
2. 降级时元数据包含 attempts / adjusted(from→to) / failure_reason；
3. merge_degradation_into_parameters 正确合并进 parameters JSON，
   且空串 / 非法 JSON / 非 dict JSON 均不丢数据；
4. HistoryRecord.parameters 白名单允许写入合并后的 JSON（DB 回归）；
5. 批量链路的降级事件结构包含 attempt / error / adjusted。
"""

import json

import pytest

from app.integrated_app.bad_case_retry import RetryResult
from app.integrated_app.engines.seedvr2_engine import ImageInferenceConfig
from app.integrated_app.history_db import HistoryDB, HistoryRecord
from app.integrated_app.services.restore_service import (
    build_degradation_metadata,
    merge_degradation_into_parameters,
)


def _degraded_retry_result(**overrides) -> RetryResult:
    base: dict = {
        "success": True,
        "attempts": 3,
        "final_params": {},
        "failure_reason": "重试耗尽，接受低质量输出: CUDA out of memory",
        "degraded": True,
    }
    base.update(overrides)
    return RetryResult(**base)


class TestBuildDegradationMetadata:
    def test_no_degradation_returns_none(self):
        result = RetryResult(success=True, attempts=1, degraded=False)
        assert (
            build_degradation_metadata({"config": ImageInferenceConfig()}, {"config": ImageInferenceConfig()}, result)
            is None
        )

    def test_none_retry_result_returns_none(self):
        assert build_degradation_metadata({}, {}, None) is None

    def test_degraded_metadata_captures_adjusted_params(self):
        original_cfg = ImageInferenceConfig(blocks_to_swap=0, resolution=2160, seed=42)
        final_cfg = ImageInferenceConfig(blocks_to_swap=4, resolution=1620, seed=999)
        meta = build_degradation_metadata(
            {"config": original_cfg},
            {"config": final_cfg},
            _degraded_retry_result(final_params={"config": final_cfg}),
        )
        assert meta is not None
        assert meta["degraded"] is True
        assert meta["attempts"] == 3
        assert meta["adjusted"]["blocks_to_swap"] == {"from": 0, "to": 4}
        assert meta["adjusted"]["resolution"] == {"from": 2160, "to": 1620}
        assert meta["adjusted"]["seed"] == {"from": 42, "to": 999}
        assert "out of memory" in meta["failure_reason"].lower()
        assert "note" in meta

    def test_flat_dict_params_supported(self):
        meta = build_degradation_metadata(
            {"resolution": 2160},
            {"resolution": 1620},
            _degraded_retry_result(final_params={"resolution": 1620}),
        )
        assert meta is not None
        assert meta["adjusted"]["resolution"] == {"from": 2160, "to": 1620}

    def test_degraded_metadata_captures_tile_params(self):
        """P0-2：TILE_DECREASE 的 tile/overlap 变化必须写入血缘。"""
        original_cfg = ImageInferenceConfig()
        final_cfg = ImageInferenceConfig(
            encode_tile_size=512,
            encode_tile_overlap=128,
            decode_tile_size=512,
            decode_tile_overlap=128,
        )
        meta = build_degradation_metadata(
            {"config": original_cfg},
            {"config": final_cfg},
            _degraded_retry_result(final_params={"config": final_cfg}),
        )
        assert meta is not None
        assert meta["adjusted"]["encode_tile_size"] == {"from": 1024, "to": 512}
        assert meta["adjusted"]["encode_tile_overlap"] == {"from": 512, "to": 128}
        assert meta["adjusted"]["decode_tile_size"] == {"from": 1024, "to": 512}
        assert meta["adjusted"]["decode_tile_overlap"] == {"from": 512, "to": 128}


class TestMergeDegradationIntoParameters:
    def test_merges_into_valid_json(self):
        original = json.dumps({"dit_model": "3b_fp16", "seed": 1}, ensure_ascii=False)
        merged = json.loads(merge_degradation_into_parameters(original, {"degraded": True, "attempts": 2}))
        assert merged["dit_model"] == "3b_fp16"
        assert merged["seed"] == 1
        assert merged["degradation"] == {"degraded": True, "attempts": 2}

    def test_empty_parameters(self):
        merged = json.loads(merge_degradation_into_parameters("", {"degraded": True}))
        assert merged == {"degradation": {"degraded": True}}

    def test_invalid_json_falls_back_to_raw(self):
        merged = json.loads(merge_degradation_into_parameters("not-json{", {"degraded": True}))
        assert merged["raw"] == "not-json{"
        assert merged["degradation"]["degraded"] is True

    def test_non_dict_json_falls_back_to_raw(self):
        merged = json.loads(merge_degradation_into_parameters("[1, 2, 3]", {"degraded": True}))
        assert merged["raw"] == "[1, 2, 3]"
        assert merged["degradation"]["degraded"] is True

    def test_none_metadata_returns_original(self):
        assert merge_degradation_into_parameters('{"a": 1}', None) == '{"a": 1}'
        assert merge_degradation_into_parameters("", None) == ""


class TestHistoryRecordDegradationWhitelist:
    @pytest.mark.asyncio
    async def test_update_record_accepts_merged_parameters(self, tmp_path):
        """验收点 4：update_record 白名单允许 parameters 列写入合并后的 JSON。"""
        async with HistoryDB(str(tmp_path / "h.db")) as db:
            record_id = await db.add_record(HistoryRecord(task_type="image", input_file="a.png", status="pending"))
            merged = merge_degradation_into_parameters(
                json.dumps({"dit_model": "3b_fp16"}, ensure_ascii=False),
                {"degraded": True, "attempts": 2, "adjusted": {"seed": {"from": 1, "to": 2}}},
            )
            assert await db.update_record(record_id, status="completed", parameters=merged)
            record = await db.get_record(record_id)
            assert record is not None
            data = json.loads(record.parameters)
            assert data["dit_model"] == "3b_fp16"
            assert data["degradation"]["adjusted"]["seed"] == {"from": 1, "to": 2}

    @pytest.mark.asyncio
    async def test_batch_event_structure_roundtrip(self, tmp_path):
        """验收点 5：批量降级事件结构可完整落库并读回。"""
        events = [
            {"attempt": 1, "error": "CUDA out of memory", "adjusted": {"blocks_to_swap": 4, "resolution": 1620}},
        ]
        params_json = json.dumps(
            {"degradation": {"degraded": True, "events": events, "note": "批量推理失败自动重试，参数已按降级阶梯调整"}},
            ensure_ascii=False,
        )
        async with HistoryDB(str(tmp_path / "h.db")) as db:
            record_id = await db.add_record(
                HistoryRecord(task_type="image", input_file="b.png", status="completed", parameters=params_json)
            )
            record = await db.get_record(record_id)
            assert record is not None
            data = json.loads(record.parameters)
            assert data["degradation"]["degraded"] is True
            assert data["degradation"]["events"][0]["attempt"] == 1
            assert data["degradation"]["events"][0]["adjusted"]["blocks_to_swap"] == 4
