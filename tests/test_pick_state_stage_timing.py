"""pick_state 分阶段耗时单元测试。"""

from __future__ import annotations

import os
from unittest.mock import patch

from pick_state.pipeline.runner import PickStatePipeline, load_pipeline_config
from pick_state.pipeline.timing import StageTimer, stage_profiling_enabled
from pick_state.pipeline.types import FrameContext
from services.event_engine.pick_state_processor import PickStateProcessor
from services.event_engine.worker import _worker_done_should_sample


def test_stage_timer_accumulates():
    timer = StageTimer(enabled=True)
    with timer.span("feature_ms"):
        with timer.span("box_ms"):
            pass
    assert timer.timings.feature_ms >= 0.0
    assert timer.timings.box_ms >= 0.0


def test_stage_profiling_env():
    with patch.dict(os.environ, {"PICK_STATE_STAGE_PROFILE": "1"}):
        assert stage_profiling_enabled()
    with patch.dict(os.environ, {"PICK_STATE_STAGE_PROFILE": "0"}, clear=False):
        assert not stage_profiling_enabled()


def test_worker_done_hot_path_no_sample():
    with patch.dict(os.environ, {"PICK_STATE_STAGE_PROFILE": "1"}):
        assert _worker_done_should_sample({"n_hits": 1, "n_picking": 0}) is False
        assert _worker_done_should_sample({"n_hits": 0, "n_action_gate": 1}) is False
        assert _worker_done_should_sample({"n_hits": 0, "n_picking": 0}) is True


def test_processor_returns_stage_timings():
    boxes = [
        {
            "box_id": "b1",
            "shelf_code": "s1",
            "video_polygon": [[0, 0], [100, 0], [100, 100], [0, 100]],
        }
    ]
    with patch.dict(os.environ, {"PICK_STATE_STAGE_PROFILE": "1"}):
        proc = PickStateProcessor(
            boxes,
            config_path="pick_state/configs/pipeline.v5_gated.json",
            video_fps=15.0,
            infer_width=640,
            infer_height=360,
            record_id="cam1",
        )
        pose = {
            "frame_idx": 10,
            "infer_width": 640,
            "infer_height": 360,
            "persons": [],
        }
        out = proc.process(pose)
        assert "stage_timings" in out
        st = out["stage_timings"]
        assert "pick_total_ms" in st
        assert "feature_ms" in st
        assert st["n_persons"] == 0


def test_pairwise_pipeline_timings_on_empty_frame():
    cfg = load_pipeline_config("pick_state/configs/pipeline.v5_gated.json")
    pipeline = PickStatePipeline(cfg)
    pipeline.configure_dims(infer_width=640, infer_height=360, video_fps=15.0)
    from pick_state.pipeline.box_trigger import BoxTrigger

    trigger = BoxTrigger([], wrist_score_min=0.15)
    timer = StageTimer(enabled=True)
    ctx = FrameContext(record_id="cam1", frame_idx=1)
    result = pipeline.process_frame(
        ctx, feature_rows=[], box_trigger=trigger, infer_height=360, timer=timer
    )
    assert result.debug.get("timings") is timer.timings
    fields = timer.timings.as_log_fields()
    assert fields["pick_total_ms"] >= 0.0
