"""OpenMMLab MMDet + MMPose 推理后端（默认，生产路径）。"""

from __future__ import annotations

import asyncio
import os

import numpy as np
from mmdet.apis import init_detector, inference_detector
from mmpose.apis import init_model as init_pose_model, inference_topdown
from mmpose.structures import merge_data_samples
from mmpose.utils import register_all_modules, adapt_mmdet_pipeline

from services.inference_backends.base import PoseBatch

register_all_modules()


class MMPoseBackend:
    name = "mmpose"

    def __init__(self, app_config: dict, executor):
        self.app_config = app_config
        self._executor = executor
        self.det_model = None
        self.pose_model = None

    def ensure_loaded(self) -> None:
        if self.det_model is not None and self.pose_model is not None:
            return

        print("🚀 正在加载 MMDet/MMPose 模型...")
        models_cfg = self.app_config["models"]
        device = str(models_cfg.get("device") or "cuda:0").strip()
        if os.environ.get("INFERENCE_USE_GPU", "0") == "1":
            if device.lower() in ("", "cpu"):
                device = "cuda:0"
        try:
            import torch

            if device.startswith("cuda") and not torch.cuda.is_available():
                raise RuntimeError(
                    "MMPose 需要 CUDA，但当前容器内 torch.cuda.is_available()=False；"
                    "请使用 visual-dps-inference-lite-gpu 镜像并 docker run --gpus all"
                )
        except ImportError as exc:
            raise RuntimeError(
                "MMPose 需要 PyTorch，请使用 visual-dps-inference-lite-gpu 或 visual-dps-inference 镜像"
            ) from exc
        print(f"ℹ️ MMPose device={device}")
        self.det_model = init_detector(
            models_cfg["det_config"],
            models_cfg["det_checkpoint"],
            device=device,
        )
        self.det_model.cfg = adapt_mmdet_pipeline(self.det_model.cfg)
        self.pose_model = init_pose_model(
            models_cfg["pose_config"],
            models_cfg["pose_checkpoint"],
            device=device,
            cfg_options=dict(model=dict(test_cfg=dict(output_heatmaps=False))),
        )

    async def detect_bboxes(self, frame) -> np.ndarray:
        loop = asyncio.get_running_loop()
        det_result = await loop.run_in_executor(
            self._executor, inference_detector, self.det_model, frame
        )
        valid = (det_result.pred_instances.labels == 0) & (det_result.pred_instances.scores > 0.3)
        return det_result.pred_instances.bboxes[valid].cpu().numpy()

    async def estimate_pose(self, frame, bboxes: np.ndarray) -> PoseBatch:
        if bboxes is None or len(bboxes) == 0:
            return PoseBatch.empty()

        loop = asyncio.get_running_loop()
        pose_results = await loop.run_in_executor(
            self._executor,
            lambda: inference_topdown(self.pose_model, frame, bboxes, bbox_format="xyxy"),
        )
        data_samples = merge_data_samples(pose_results)
        return PoseBatch(
            keypoints=np.asarray(data_samples.pred_instances.keypoints, dtype=np.float32),
            keypoint_scores=np.asarray(data_samples.pred_instances.keypoint_scores, dtype=np.float32),
        )
