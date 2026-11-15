"""MLOps P1-1：实际种子回写 history parameters 血缘测试。

验收标准（对应 MLOps 评估报告 P1-1「seed=-1 时实际种子未留档」修复）：
1. merge_provenance_into_parameters 纯函数：合法 JSON 合并保留原键、空 provenance 原样、
   空串/非法 JSON/非 dict JSON 不丢数据（raw 兜底）；
2. 视频血缘链完整：apply_ffmpeg_lineage → merge_degradation → merge_provenance
   三段合并互不覆盖（重写 parameters 不得丢失创建期 ffmpeg 版本血缘）；
3. process_image_task 成功链路：引擎 metadata.seed_effective 写入 history parameters；
   metadata 无 seed_effective 且未降级时不得触碰 parameters 列。
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.integrated_app.config_models import ImageRestoreParams
from app.integrated_app.engine_interface import RestoreResult
from app.integrated_app.services import restore_service
from app.integrated_app.services.restore_service import (
    apply_ffmpeg_lineage,
    merge_degradation_into_parameters,
    merge_provenance_into_parameters,
    process_image_task,
)


class TestMergeProvenanceIntoParameters:
    def test_merges_and_preserves_original_keys(self):
        original = json.dumps({"dit_model": "3b_fp16", "seed": -1}, ensure_ascii=False)
        merged = json.loads(merge_provenance_into_parameters(original, {"seed_effective": 777}))
        assert merged["dit_model"] == "3b_fp16"
        assert merged["seed"] == -1  # 用户请求语义保留（-1=随机），实际值在 seed_effective
        assert merged["seed_effective"] == 777

    def test_empty_provenance_returns_input(self):
        original = json.dumps({"a": 1})
        assert merge_provenance_into_parameters(original, {}) == original

    def test_empty_parameters_json(self):
        merged = json.loads(merge_provenance_into_parameters("", {"seed_effective": 1}))
        assert merged == {"seed_effective": 1}

    def test_invalid_json_wrapped_in_raw(self):
        merged = json.loads(merge_provenance_into_parameters("not-json{", {"seed_effective": 2}))
        assert merged["raw"] == "not-json{"
        assert merged["seed_effective"] == 2

    def test_non_dict_json_wrapped_in_raw(self):
        merged = json.loads(merge_provenance_into_parameters("[1,2]", {"seed_effective": 3}))
        assert merged["raw"] == "[1,2]"
        assert merged["seed_effective"] == 3


class TestVideoLineageChain:
    def test_ffmpeg_degradation_seed_three_way_merge(self, monkeypatch):
        # 重写 parameters 会整体覆盖创建期值：链式注入后 ffmpeg 血缘必须在
        monkeypatch.setattr(
            "app.integrated_app.video_processor.get_ffmpeg_version", lambda *a, **k: "ffmpeg version 7.1-test"
        )
        base = json.dumps({"resolution": 1280, "seed": -1}, ensure_ascii=False)
        merged = apply_ffmpeg_lineage(base, "video")
        merged = merge_degradation_into_parameters(merged, {"degraded": True, "attempts": 2})
        merged = merge_provenance_into_parameters(merged, {"seed_effective": 123})
        data = json.loads(merged)
        assert data["ffmpeg_version"] == "ffmpeg version 7.1-test"
        assert data["degradation"] == {"degraded": True, "attempts": 2}
        assert data["seed_effective"] == 123
        assert data["resolution"] == 1280

    def test_image_lineage_is_noop(self):
        base = json.dumps({"resolution": 512})
        assert apply_ffmpeg_lineage(base, "image") == base


def _patch_service_dependencies(monkeypatch, engine):
    """把 process_image_task 依赖的模块级单例替换为 mock，返回捕获用的 mock 集合。"""
    mocks = {
        "model_registry": MagicMock(touch_activity=MagicMock(), get_engine=MagicMock(return_value=engine)),
        "task_state_store": MagicMock(update=AsyncMock(), update_cached=MagicMock()),
        "metrics_collector": MagicMock(record_inference=MagicMock()),
        "oom_breaker": MagicMock(record_success=MagicMock(), record_failure=MagicMock()),
        "vram_leak_detector": MagicMock(),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(restore_service, name, mock)
    monkeypatch.setattr(
        restore_service,
        "_load_app_config_dict",
        lambda: {"runtime": {"retry": {"enabled": False}}},
    )
    return mocks


@pytest.fixture
def fake_history_db():
    db = MagicMock()
    db.update_record = AsyncMock()
    return db


@pytest.fixture
def fake_task_queue():
    q = MagicMock()
    q.is_cancelled = MagicMock(return_value=False)
    return q


class TestProcessImageTaskSeedLineage:
    async def test_seed_effective_written_into_parameters(
        self, monkeypatch, tmp_path, fake_history_db, fake_task_queue
    ):
        engine = MagicMock()
        engine.set_progress_callback = MagicMock()
        engine.infer_image = AsyncMock(
            return_value=RestoreResult(
                success=True,
                output_path=None,
                processing_time=1.2,
                metadata={"seed_effective": 777},
            )
        )
        _patch_service_dependencies(monkeypatch, engine)
        params = ImageRestoreParams(seed=-1)

        await process_image_task(
            "t-seed",
            42,
            str(tmp_path / "in.png"),
            params,
            fake_history_db,
            fake_task_queue,
        )

        params_updates = [c for c in fake_history_db.update_record.await_args_list if "parameters" in c.kwargs]
        assert len(params_updates) == 1, "成功链路必须恰好一次 parameters 回写"
        data = json.loads(params_updates[0].kwargs["parameters"])
        assert data["seed_effective"] == 777
        assert data["seed"] == -1  # 请求语义保留
        assert data["dit_model"] == "3b_fp16"

    async def test_no_seed_effective_no_parameters_touch(self, monkeypatch, tmp_path, fake_history_db, fake_task_queue):
        engine = MagicMock()
        engine.set_progress_callback = MagicMock()
        engine.infer_image = AsyncMock(
            return_value=RestoreResult(success=True, output_path=None, processing_time=1.0, metadata={})
        )
        _patch_service_dependencies(monkeypatch, engine)
        params = ImageRestoreParams(seed=5)

        await process_image_task("t-noseed", 43, str(tmp_path / "in.png"), params, fake_history_db, fake_task_queue)

        # completed 状态更新照常，但不得有无谓的 parameters 重写
        status_updates = [c for c in fake_history_db.update_record.await_args_list if "parameters" in c.kwargs]
        assert status_updates == []
