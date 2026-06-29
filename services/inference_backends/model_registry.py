"""推理模型预设：models.backend 存 preset id（如 rtmpose_t、yolo26s_pose）；models.det 仅 RTMPose 使用。"""

from __future__ import annotations

from dataclasses import dataclass

BACKEND_RTMPOSE_ONNX = "rtmpose_onnx"
BACKEND_YOLO_POSE = "yolo_pose"

_ONNX_SDK = "https://download.openmmlab.com/mmpose/v1/projects/rtmposev1/onnx_sdk"
_DET_NANO = f"{_ONNX_SDK}/rtmdet_nano_8xb32-100e_coco-obj365-person-05d8511e.zip"
_DET_M = f"{_ONNX_SDK}/rtmdet_m_8xb32-100e_coco-obj365-person-235e8209.zip"

DEFAULT_DET_VARIANT = "nano"
ALLOWED_DET_VARIANTS = frozenset({"nano", "m"})


@dataclass(frozen=True)
class ModelPreset:
    id: str
    family: str
    variant: str
    label: str
    short_label: str


RTMPoseOnnxPreset = ModelPreset  # alias


_PRESETS: dict[str, ModelPreset] = {}
_ALIASES: dict[str, str] = {
    "lite": "rtmpose_t",
    "mp": "rtmpose_t",
    "mediapipe": "rtmpose_t",
    "mmpose": "rtmpose_t",
    "mm": "rtmpose_t",
    "default": "rtmpose_t",
    "rtmpose_onnx": "rtmpose_t",
    "rtmpose-t": "rtmpose_t",
    "rtmpose_t": "rtmpose_t",
    "yolo_pose": "yolo26s_pose",
}


def _reg(p: ModelPreset) -> None:
    _PRESETS[p.id] = p


_reg(
    ModelPreset(
        "rtmpose_t",
        BACKEND_RTMPOSE_ONNX,
        "t",
        "RTMPose-T（ONNX）",
        "RTMPose-T",
    )
)
_reg(
    ModelPreset(
        "rtmpose_s",
        BACKEND_RTMPOSE_ONNX,
        "s",
        "RTMPose-S（ONNX）",
        "RTMPose-S",
    )
)
_reg(
    ModelPreset(
        "rtmpose_m",
        BACKEND_RTMPOSE_ONNX,
        "m",
        "RTMPose-M（ONNX）",
        "RTMPose-M",
    )
)
for _vid, _label, _short in (
    ("n", "YOLO26n-pose", "YOLO26n"),
    ("s", "YOLO26s-pose", "YOLO26s"),
    ("m", "YOLO26m-pose", "YOLO26m"),
    ("l", "YOLO26l-pose", "YOLO26l"),
):
    _reg(
        ModelPreset(
            f"yolo26{_vid}_pose",
            BACKEND_YOLO_POSE,
            _vid,
            _label,
            _short,
        )
    )

DEFAULT_PRESET_ID = "rtmpose_t"
ALLOWED_PRESET_IDS = frozenset(_PRESETS.keys())
LITE_BACKEND_FAMILIES = frozenset({BACKEND_RTMPOSE_ONNX, BACKEND_YOLO_POSE})

RTMDET_ASSETS: dict[str, dict[str, str | tuple[int, int]]] = {
    "nano": {
        "det_dir": "rtmdet_nano",
        "det_url": _DET_NANO,
        "det_size": (320, 320),
    },
    "m": {
        "det_dir": "rtmdet_m",
        "det_url": _DET_M,
        "det_size": (640, 640),
    },
}

RTMPOSE_POSE_ASSETS: dict[str, dict[str, str | tuple[int, int]]] = {
    "t": {
        "pose_dir": "rtmpose_t",
        "pose_url": (
            f"{_ONNX_SDK}/rtmpose-t_simcc-body7_pt-body7_420e-256x192-026a1439_20230504.zip"
        ),
        "pose_size": (192, 256),
    },
    "s": {
        "pose_dir": "rtmpose_s",
        "pose_url": (
            f"{_ONNX_SDK}/rtmpose-s_simcc-body7_pt-body7_420e-256x192-acd4a1ef_20230504.zip"
        ),
        "pose_size": (192, 256),
    },
    "m": {
        "pose_dir": "rtmpose_m",
        "pose_url": (
            f"{_ONNX_SDK}/rtmpose-m_simcc-body7_pt-body7_420e-256x192-e48f03d0_20230504.zip"
        ),
        "pose_size": (192, 256),
    },
}

# 兼容旧 import：默认 det=nano
RTMPOSE_VARIANT_ASSETS: dict[str, dict[str, str | tuple[int, int]]] = {
    variant: {
        **RTMDET_ASSETS["nano"],
        **pose,
    }
    for variant, pose in RTMPOSE_POSE_ASSETS.items()
}


def resolve_model_preset(
    app_config: dict | None = None,
    overrides: dict | None = None,
) -> ModelPreset:
    def _raw() -> str:
        if isinstance(overrides, dict):
            v = str(overrides.get("models.backend", "")).strip().lower()
            if v:
                return v
        import os

        env = os.environ.get("INFERENCE_BACKEND", "").strip().lower()
        if env:
            return env
        if isinstance(app_config, dict):
            models = app_config.get("models")
            if isinstance(models, dict):
                v = str(models.get("backend", "")).strip().lower()
                if v:
                    return v
        return ""

    key = _ALIASES.get(_raw(), _raw())
    if not key:
        return _PRESETS[DEFAULT_PRESET_ID]
    return _PRESETS.get(key, _PRESETS[DEFAULT_PRESET_ID])


def resolve_det_variant(
    app_config: dict | None = None,
    overrides: dict | None = None,
) -> str:
    def _raw() -> str:
        if isinstance(overrides, dict):
            v = str(overrides.get("models.det", "")).strip().lower()
            if v:
                return v
        import os

        env = os.environ.get("INFERENCE_RTM_DET", "").strip().lower()
        if env:
            return env
        if isinstance(app_config, dict):
            models = app_config.get("models")
            if isinstance(models, dict):
                v = str(models.get("det", "")).strip().lower()
                if v:
                    return v
        return ""

    key = _raw() or DEFAULT_DET_VARIANT
    if key not in ALLOWED_DET_VARIANTS:
        return DEFAULT_DET_VARIANT
    return key


def resolve_rtm_assets(
    pose_variant: str,
    det_variant: str | None = None,
) -> dict[str, str | tuple[int, int]]:
    """合并 RTMDet + RTMPose 资产（供 ONNX 后端加载）。"""
    pose_key = str(pose_variant or "t").lower()
    det_key = str(det_variant or DEFAULT_DET_VARIANT).lower()
    if pose_key not in RTMPOSE_POSE_ASSETS:
        pose_key = "t"
    if det_key not in RTMDET_ASSETS:
        det_key = DEFAULT_DET_VARIANT
    return {**RTMDET_ASSETS[det_key], **RTMPOSE_POSE_ASSETS[pose_key]}


def resolve_backend_family(
    app_config: dict | None = None,
    overrides: dict | None = None,
) -> str:
    return resolve_model_preset(app_config, overrides).family


def normalize_backend_setting(raw: str) -> str:
    key = _ALIASES.get(str(raw or "").strip().lower(), str(raw or "").strip().lower())
    if key not in _PRESETS:
        raise ValueError(
            f"backend must be one of: {', '.join(sorted(_PRESETS.keys()))}"
        )
    return key


def normalize_det_setting(raw: str) -> str:
    key = str(raw or "").strip().lower()
    if key not in ALLOWED_DET_VARIANTS:
        raise ValueError(f"det must be one of: {', '.join(sorted(ALLOWED_DET_VARIANTS))}")
    return key


YOLO_VARIANT_WEIGHTS: dict[str, str] = {
    "n": "yolo26n-pose.pt",
    "s": "yolo26s-pose.pt",
    "m": "yolo26m-pose.pt",
    "l": "yolo26l-pose.pt",
}
