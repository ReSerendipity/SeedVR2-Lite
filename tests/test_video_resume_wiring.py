"""段级帧续跑接线测试（评估 P2-6）。

覆盖三层契约：
- 引擎配置层：frames_dir_override / resume_frames 经 kwargs 进入 infer 配置；
- 服务编排层：process_video_task 始终使用任务隔离帧目录 _frames_<task_id>；
- 崩溃恢复层：recover_tasks 对视频任务以 resume_frames=True 重新提交。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.integrated_app.engines.seedvr2_engine import SeedVR2Engine
from app.integrated_app.model_registry import model_registry
from app.integrated_app.services.restore_service import process_video_task


class TestEngineInferConfigThreading:
    def test_frames_dir_override_flows_into_infer_config(self):
        engine = SeedVR2Engine({})
        inf = engine._get_inference_config(frames_dir_override="/tmp/_frames_t1")
        assert inf["frames_dir_override"] == "/tmp/_frames_t1"

    def test_frames_dir_override_defaults_to_empty(self):
        engine = SeedVR2Engine({})
        inf = engine._get_inference_config()
        assert inf["frames_dir_override"] == ""

    def test_resume_frames_flows_into_infer_config(self):
        engine = SeedVR2Engine({})
        assert engine._get_inference_config(resume_frames=True)["resume_frames"] is True
        assert engine._get_inference_config()["resume_frames"] is False


def _make_stubs(task_id: str):
    """构造 run_task_with_state 所需的最小依赖桩"""

    class _Queue:
        def is_cancelled(self, _task_id: str) -> bool:
            return False

    class _Engine:
        def __init__(self):
            self.calls: list[dict] = []

        def set_progress_callback(self, cb):
            return None

        async def infer_video(self, **kwargs):
            self.calls.append(kwargs)
            return MagicMock(success=True, output_path="out.mp4", processing_time=1.0, metadata={})

    engine = _Engine()
    monkey_target = model_registry
    return _Queue(), engine, monkey_target


class TestProcessVideoTaskWiring:
    @pytest.mark.asyncio
    async def test_task_scoped_frames_dir_and_resume_intent(self, monkeypatch):
        from app.integrated_app.config_models import VideoRestoreParams

        queue, engine, _registry = _make_stubs("vt-resume-1")
        monkeypatch.setattr(model_registry, "get_engine", lambda: engine)

        history_db = AsyncMock()
        params = VideoRestoreParams(resolution=720)

        await process_video_task(
            task_id="vt-resume-1",
            record_id=1,
            input_path="input.mp4",
            model_size="3b",
            params=params,
            history_db=history_db,
            task_queue=queue,
        )

        assert len(engine.calls) == 1
        kwargs = engine.calls[0]
        # 帧目录必须任务隔离（P2-6 防跨任务污染）
        assert kwargs["frames_dir_override"].endswith("_frames_vt-resume-1")
        assert "outputs" in kwargs["frames_dir_override"]
        # 默认启用自动重试（build_retry_config 默认 max_retries=3）→ 续跑意图开启，
        # 引擎失败路径保留段帧供重试复用
        assert kwargs["resume_frames"] is True
        # P0-2：tile 参数必须显式透传给引擎，TILE_DECREASE 才能落到 _vae_tiled_config
        assert kwargs["encode_tile_size"] == 1024
        assert kwargs["encode_tile_overlap"] == 128
        assert kwargs["decode_tile_size"] == 1024
        assert kwargs["decode_tile_overlap"] == 128

    @pytest.mark.asyncio
    async def test_recovery_resume_flag_propagates(self, monkeypatch):
        """recover_tasks 必须以 resume_frames=True 重新提交视频任务"""
        from app.integrated_app.config_models import VideoRestoreParams
        from app.integrated_app.routes.restore import recovery as recovery_module

        submitted: list = []

        class _Queue:
            async def submit(self, task_id, factory, on_cancel=None):
                submitted.append((task_id, factory))

        class _TaskRecord:
            task_id = "vt-resume-2"
            record_id = 7

        class _Record:
            id = 7
            task_type = "video"
            input_file = "input.mp4"
            model_size = "3b"
            parameters = VideoRestoreParams(resolution=720).model_dump_json()

        history_db = AsyncMock()
        history_db.get_incomplete_tasks = AsyncMock(return_value=[_TaskRecord()])
        history_db.get_records_by_ids = AsyncMock(return_value=[_Record()])

        monkeypatch.setattr(recovery_module, "process_video_task", AsyncMock(return_value=None))

        recovered = await recovery_module.recover_tasks(history_db, _Queue(), {})
        assert recovered == 1
        assert len(submitted) == 1

        # 手动触发提交的协程工厂，验证 resume_frames=True 传入编排层
        _task_id, factory = submitted[0]
        await factory()
        recovery_module.process_video_task.assert_called_once()
        args, kwargs = recovery_module.process_video_task.call_args
        assert kwargs.get("resume_frames") is True or (len(args) >= 8 and args[7] is True)
