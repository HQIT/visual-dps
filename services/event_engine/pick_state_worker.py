"""EventRedisWorker 变体：用 PickStateProcessor 替代硬规则 CollisionProcessor。"""

from __future__ import annotations

import logging
import os

from services.event_engine.annotation_boxes import load_scaled_boxes
from services.event_engine.pick_state_processor import PickStateProcessor, _DEFAULT_CONFIG
from services.event_engine.worker import EventRedisWorker, _CameraContext

logger = logging.getLogger(__name__)


class PickStateRedisWorker(EventRedisWorker):
    """同 pose 流 / 同 consumer group；算法换成 pick_state v5_gated。"""

    def __init__(self, app_config: dict, callback_reporter=None):
        super().__init__(app_config, callback_reporter=callback_reporter)
        self._pick_config = (
            os.environ.get("PICK_STATE_CONFIG", "").strip() or str(_DEFAULT_CONFIG)
        )

    def _get_processor(self, camera_id: str, infer_w: int, infer_h: int):
        self._refresh_runtime_settings_if_needed()
        json_path = self._resolve_json_path(camera_id)
        ctx = self._contexts.get(camera_id)
        mtime = os.path.getmtime(json_path) if os.path.isfile(json_path) else 0.0

        if (
            ctx is None
            or ctx.json_path != json_path
            or ctx.json_mtime != mtime
            or ctx.infer_w != infer_w
            or ctx.infer_h != infer_h
        ):
            boxes = (
                load_scaled_boxes(json_path, infer_w, infer_h)
                if infer_w > 0 and infer_h > 0
                else []
            )
            if not boxes:
                logger.warning(
                    "pick-state worker: no boxes for camera=%s path=%s",
                    camera_id,
                    json_path,
                )
            processor = PickStateProcessor(
                boxes,
                config_path=self._pick_config,
                video_fps=self._video_fps,
                infer_width=infer_w,
                infer_height=infer_h,
                record_id=camera_id,
            )
            ctx = _CameraContext(
                json_path=json_path,
                json_mtime=mtime,
                processor=processor,  # type: ignore[arg-type]
                prefilter=None,  # pick_state 自带门控，不用硬规则 prefilter
                infer_w=infer_w,
                infer_h=infer_h,
            )
            self._contexts[camera_id] = ctx
        return ctx.processor
