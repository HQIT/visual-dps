"""Ultralytics YOLO26-pose（n/s/m/l），单阶段检测+姿态。"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass

import numpy as np

from services.inference_backends.base import PoseBatch
from services.inference_backends.model_registry import YOLO_VARIANT_WEIGHTS
from services.pipeline_log import get_inference_logger


@dataclass
class _YoloFrameResult:
    boxes: np.ndarray
    pose: PoseBatch


class YoloPoseBackend:
    name = "yolo_pose"

    def __init__(self, app_config: dict, executor, *, variant: str = "s"):
        self.app_config = app_config
        self._executor = executor
        self._variant = str(variant or "s").lower()
        if self._variant not in YOLO_VARIANT_WEIGHTS:
            self._variant = "s"
        self._model = None
        self._pending: _YoloFrameResult | None = None

    def _models_dir(self) -> str:
        return os.path.join(
            self.app_config.get("paths", {}).get("base_localdata_dir", "localdata"),
            "models",
            "yolo_pose",
        )

    def _resolve_weight_path(self) -> str:
        models_cfg = self.app_config.get("models", {})
        key = f"yolo_pose_{self._variant}_weights"
        custom = str(models_cfg.get(key) or "").strip()
        if custom and os.path.isfile(custom):
            return custom
        filename = YOLO_VARIANT_WEIGHTS[self._variant]
        local = os.path.join(self._models_dir(), filename)
        if os.path.isfile(local):
            return local
        return custom or filename

    def ensure_loaded(self) -> None:
        if self._model is not None:
            return
        from ultralytics import YOLO

        weight = self._resolve_weight_path()
        models_dir = self._models_dir()
        os.makedirs(models_dir, exist_ok=True)
        os.environ.setdefault("YOLO_CONFIG_DIR", models_dir)

        device = "cpu"
        if os.environ.get("INFERENCE_USE_GPU", "").strip() in ("1", "true", "yes"):
            device = str(
                self.app_config.get("models", {}).get("yolo_pose_device") or "0"
            ).strip()

        infer_log = get_inference_logger()
        infer_log.info(f"🚀 正在加载 {weight}（Ultralytics YOLO26-pose）…")
        self._model = YOLO(weight)
        self._device = device
        if str(device).strip().lower() not in ("cpu", ""):
            try:
                probe = np.zeros((64, 64, 3), dtype=np.uint8)
                self._model.predict(probe, device=device, verbose=False, conf=0.99)
            except Exception as exc:
                infer_log.warning(
                    f"⚠️ YOLO CUDA 预热失败（{exc!r}），回退 CPU；"
                    "旧 Pascal GPU 请用 CPU 或换 Turing+ 测 GPU"
                )
                self._device = "cpu"
        infer_log.info(f"✅ YOLO26-{self._variant}-pose 已就绪: {weight} device={self._device}")

    def _infer_sync(self, frame) -> _YoloFrameResult:
        self.ensure_loaded()
        verbose = bool(self.app_config.get("debug-info", {}).get("enabled", False))
        results = self._model.predict(
            frame,
            device=self._device,
            verbose=verbose,
            conf=float(self.app_config.get("models", {}).get("yolo_pose_conf", 0.25)),
        )
        if not results:
            return _YoloFrameResult(
                boxes=np.empty((0, 4), dtype=np.float32),
                pose=PoseBatch.empty(),
            )
        r0 = results[0]
        boxes = np.empty((0, 4), dtype=np.float32)
        if r0.boxes is not None and len(r0.boxes):
            xyxy = r0.boxes.xyxy.cpu().numpy()
            boxes = np.asarray(xyxy, dtype=np.float32)

        if r0.keypoints is None or r0.keypoints.data is None or len(r0.keypoints.data) == 0:
            return _YoloFrameResult(boxes=boxes, pose=PoseBatch.empty())

        kdata = r0.keypoints.data.cpu().numpy()
        # (N, 17, 3) -> xy + score
        if kdata.ndim == 2:
            kdata = kdata.reshape(1, -1, 3)
        kpts = kdata[..., :2].astype(np.float32)
        scores = kdata[..., 2].astype(np.float32)
        if boxes.shape[0] == 0 and kpts.shape[0] > 0:
            boxes = _boxes_from_keypoints(kpts)
        return _YoloFrameResult(
            boxes=boxes,
            pose=PoseBatch(keypoints=kpts, keypoint_scores=scores),
        )

    async def detect_bboxes(self, frame) -> np.ndarray:
        loop = asyncio.get_running_loop()
        self._pending = await loop.run_in_executor(self._executor, self._infer_sync, frame)
        return self._pending.boxes

    async def estimate_pose(self, frame, bboxes: np.ndarray) -> PoseBatch:
        if self._pending is not None:
            batch = self._pending.pose
            self._pending = None
            return batch
        loop = asyncio.get_running_loop()
        pending = await loop.run_in_executor(self._executor, self._infer_sync, frame)
        return pending.pose


def _boxes_from_keypoints(kpts: np.ndarray) -> np.ndarray:
    out = []
    for person in kpts:
        xs = person[:, 0]
        ys = person[:, 1]
        valid = (xs > 0) | (ys > 0)
        if not np.any(valid):
            continue
        out.append([xs[valid].min(), ys[valid].min(), xs[valid].max(), ys[valid].max()])
    if not out:
        return np.empty((0, 4), dtype=np.float32)
    return np.asarray(out, dtype=np.float32)
