"""RTMDet + RTMPose t/s/m（ONNX Runtime，det 可选 nano / m）。"""

from __future__ import annotations

import asyncio
import os

import numpy as np

from core.ort_runtime import (
    build_ort_session_options,
    ort_session_summary,
    rebind_rtmlib_ort_session,
)
from services.inference_backends.base import PoseBatch
from services.inference_backends.model_registry import (
    DEFAULT_DET_VARIANT,
    RTMPOSE_POSE_ASSETS,
    resolve_det_variant,
    resolve_rtm_assets,
)
from services.inference_backends.onnx_assets import ensure_onnx_from_zip
from services.pipeline_log import get_inference_logger


def _models_dir(app_config: dict) -> str:
    base = app_config.get("paths", {}).get("base_localdata_dir", "localdata")
    return os.path.join(base, "models", "rtmpose_onnx")


def _inference_wants_gpu() -> bool:
    return os.environ.get("INFERENCE_USE_GPU", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _is_cuda_device(device: str) -> bool:
    return str(device or "").strip().lower() in ("cuda", "gpu")


def _preload_ort_cuda_dlls(device: str) -> None:
    if not _is_cuda_device(device):
        return
    try:
        from services.nvidia_pip_cuda import preload_cudnn_libs

        preload_cudnn_libs()
        import onnxruntime as ort

        if hasattr(ort, "preload_dlls"):
            try:
                ort.preload_dlls(cuda=True, cudnn=True)
            except TypeError:
                ort.preload_dlls()
    except Exception as exc:
        get_inference_logger().warning(f"⚠️ onnxruntime CUDA 库预加载失败: {exc}")


def _ort_active_provider(onnx_path: str, sess_options) -> str:
    import onnxruntime as ort

    sess = ort.InferenceSession(
        onnx_path,
        sess_options=sess_options,
        providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
    )
    try:
        return sess.get_providers()[0]
    finally:
        del sess


def _resolve_model_path(app_config: dict, subdir: str) -> str:
    return os.path.join(_models_dir(app_config), subdir, "end2end.onnx")


def _apply_rtmlib_ort_options(tool, sess_options) -> None:
    rebind_rtmlib_ort_session(tool, sess_options)


class RTMPoseOnnxBackend:
    name = "rtmpose_onnx"

    def __init__(
        self,
        app_config: dict,
        executor,
        *,
        variant: str = "t",
        det_variant: str | None = None,
    ):
        self.app_config = app_config
        self._executor = executor
        self._variant = str(variant or "t").lower()
        if self._variant not in RTMPOSE_POSE_ASSETS:
            self._variant = "t"
        self._det_variant = str(det_variant or resolve_det_variant(app_config) or DEFAULT_DET_VARIANT).lower()
        self._det = None
        self._pose = None

    def ensure_loaded(self) -> None:
        if self._det is not None and self._pose is not None:
            return

        from rtmlib.tools.object_detection.rtmdet import RTMDet
        from rtmlib.tools.pose_estimation.rtmpose import RTMPose

        assets = resolve_rtm_assets(self._variant, self._det_variant)
        models_cfg = self.app_config.get("models", {})
        det_path = _resolve_model_path(self.app_config, str(assets["det_dir"]))
        pose_path = _resolve_model_path(self.app_config, str(assets["pose_dir"]))
        det_url = str(assets["det_url"]).strip()
        pose_url = str(assets["pose_url"]).strip()

        det_size = assets["det_size"]
        pose_size = assets["pose_size"]
        det_input_size = (int(det_size[0]), int(det_size[1]))
        pose_input_size = (int(pose_size[0]), int(pose_size[1]))

        infer_log = get_inference_logger()
        sess_options = build_ort_session_options()
        infer_log.info(
            f"🚀 正在加载 RTMDet-{self._det_variant.upper()} + RTMPose-{self._variant.upper()}（ONNX）… "
            f"ORT {ort_session_summary(sess_options)}"
        )
        ensure_onnx_from_zip(det_path, det_url)
        ensure_onnx_from_zip(pose_path, pose_url)

        backend = str(models_cfg.get("rtmpose_onnx_ort_backend") or "onnxruntime").strip()
        device = str(models_cfg.get("rtmpose_onnx_device") or "cpu").strip()
        if _inference_wants_gpu():
            device = str(models_cfg.get("rtmpose_onnx_device_gpu") or "cuda").strip()

        def _load_models(dev: str) -> None:
            if _is_cuda_device(dev):
                _preload_ort_cuda_dlls(dev)
            self._det = RTMDet(
                onnx_model=det_path,
                model_input_size=det_input_size,
                backend=backend,
                device=dev,
            )
            self._pose = RTMPose(
                onnx_model=pose_path,
                model_input_size=pose_input_size,
                backend=backend,
                device=dev,
            )
            if backend == "onnxruntime":
                _apply_rtmlib_ort_options(self._det, sess_options)
                _apply_rtmlib_ort_options(self._pose, sess_options)

        try:
            _load_models(device)
            if _is_cuda_device(device):
                active = _ort_active_provider(det_path, sess_options)
                if active != "CUDAExecutionProvider":
                    infer_log.warning(
                        f"⚠️ ORT 实际 EP={active}（期望 CUDAExecutionProvider），回退 CPU"
                    )
                    device = "cpu"
                    self._det = None
                    self._pose = None
                    _load_models(device)
        except Exception as exc:
            if not _is_cuda_device(device):
                raise
            infer_log.warning(
                f"⚠️ RTMPose ONNX CUDA 初始化失败（{exc!r}），回退 CPU；"
                "旧 Pascal GPU（如 GTX 1080）请用 CPU 或换 Turing+ 显卡测 GPU"
            )
            device = "cpu"
            self._det = None
            self._pose = None
            _load_models(device)

        if backend == "onnxruntime":
            det_ep = self._det.session.get_providers()[0] if self._det else "?"
            pose_ep = self._pose.session.get_providers()[0] if self._pose else "?"
            infer_log.info(
                f"✅ RTMDet-{self._det_variant.upper()} + RTMPose-{self._variant.upper()} ONNX 已就绪: "
                f"det={det_path} pose={pose_path} device={device} "
                f"ep=({det_ep},{pose_ep}) {ort_session_summary(sess_options)}"
            )
        else:
            infer_log.info(
                f"✅ RTMDet-{self._det_variant.upper()} + RTMPose-{self._variant.upper()} ONNX 已就绪: "
                f"det={det_path} pose={pose_path} device={device}"
            )

    def _detect_sync(self, frame) -> np.ndarray:
        boxes = self._det(frame)
        if boxes is None or len(boxes) == 0:
            return np.empty((0, 4), dtype=np.float32)
        arr = np.asarray(boxes, dtype=np.float32)
        if arr.ndim == 1:
            arr = arr.reshape(1, -1)
        return arr[:, :4]

    def _pose_sync(self, frame, bboxes: np.ndarray) -> PoseBatch:
        bbox_list = bboxes[:, :4].tolist()
        keypoints, scores = self._pose(frame, bboxes=bbox_list)
        if keypoints is None or len(keypoints) == 0:
            return PoseBatch.empty()
        kpts = np.asarray(keypoints, dtype=np.float32)
        sc = np.asarray(scores, dtype=np.float32)
        if kpts.ndim == 2:
            kpts = kpts.reshape(1, -1, 2)
        if sc.ndim == 1:
            sc = sc.reshape(1, -1)
        return PoseBatch(keypoints=kpts, keypoint_scores=sc)

    async def detect_bboxes(self, frame) -> np.ndarray:
        self.ensure_loaded()
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self._detect_sync, frame)

    async def estimate_pose(self, frame, bboxes: np.ndarray) -> PoseBatch:
        if bboxes is None or len(bboxes) == 0:
            return PoseBatch.empty()
        self.ensure_loaded()
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self._pose_sync, frame, bboxes)
