"""可插拔推理后端：rtmpose_onnx、yolo_pose（多档位见 model_registry）。"""

from __future__ import annotations

from services.inference_backends.model_registry import (
    ALLOWED_PRESET_IDS,
    BACKEND_RTMPOSE_ONNX,
    BACKEND_YOLO_POSE,
    DEFAULT_PRESET_ID,
    LITE_BACKEND_FAMILIES,
    ModelPreset,
    normalize_backend_setting,
    resolve_backend_family,
    resolve_det_variant,
    resolve_model_preset,
)

# 兼容旧 import
_LITE_BACKENDS = LITE_BACKEND_FAMILIES
_ALIASES = {}  # 已迁至 model_registry._ALIASES


def resolve_backend_name(
    app_config: dict | None = None,
    overrides: dict | None = None,
) -> str:
    """返回模型 preset id（写入状态/日志），如 rtmpose_t、yolo26s_pose。"""
    return resolve_model_preset(app_config, overrides).id


def create_inference_backend(app_config: dict, executor):
    preset = resolve_model_preset(app_config)
    if preset.family == BACKEND_RTMPOSE_ONNX:
        from services.inference_backends.rtmpose_onnx_backend import RTMPoseOnnxBackend

        return RTMPoseOnnxBackend(
            app_config,
            executor,
            variant=preset.variant,
            det_variant=resolve_det_variant(app_config),
        )
    if preset.family == BACKEND_YOLO_POSE:
        from services.inference_backends.yolo_pose_backend import YoloPoseBackend

        return YoloPoseBackend(app_config, executor, variant=preset.variant)
    raise RuntimeError(f"未知推理后端: {preset.family} (preset={preset.id})")
