"""摄像头推理来源与边缘能力（与 source_type 正交）。"""

from __future__ import annotations

INFERENCE_MODE_CENTRAL = "central"
INFERENCE_MODE_EDGE = "edge"
INFERENCE_MODE_DISABLED = "disabled"

INFERENCE_MODES = frozenset(
    {INFERENCE_MODE_CENTRAL, INFERENCE_MODE_EDGE, INFERENCE_MODE_DISABLED}
)

EDGE_CAPABILITY_POSE = "pose"
EDGE_CAPABILITY_VIDEO = "video"
EDGE_CAPABILITY_COLLISION = "collision"

EDGE_CAPABILITIES = frozenset(
    {EDGE_CAPABILITY_POSE, EDGE_CAPABILITY_VIDEO, EDGE_CAPABILITY_COLLISION}
)


def normalize_inference_mode(raw) -> str:
    mode = str(raw or INFERENCE_MODE_CENTRAL).strip().lower()
    if mode not in INFERENCE_MODES:
        return INFERENCE_MODE_CENTRAL
    return mode


def normalize_edge_capabilities(raw) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        parts = [p.strip() for p in raw.split(",") if p.strip()]
    elif isinstance(raw, (list, tuple)):
        parts = [str(p).strip() for p in raw if str(p).strip()]
    else:
        return []
    out: list[str] = []
    for item in parts:
        key = item.lower()
        if key in EDGE_CAPABILITIES and key not in out:
            out.append(key)
    return out


def get_inference_mode(camera: dict | None) -> str:
    if not isinstance(camera, dict):
        return INFERENCE_MODE_CENTRAL
    return normalize_inference_mode(camera.get("inference_mode"))


def get_edge_capabilities(camera: dict | None) -> list[str]:
    if not isinstance(camera, dict):
        return []
    return normalize_edge_capabilities(camera.get("edge_capabilities"))


def is_edge_camera(camera: dict | None) -> bool:
    return get_inference_mode(camera) == INFERENCE_MODE_EDGE


def edge_expects_pose(camera: dict | None) -> bool:
    if not is_edge_camera(camera):
        return False
    caps = get_edge_capabilities(camera)
    return not caps or EDGE_CAPABILITY_POSE in caps


def edge_expects_collision(camera: dict | None) -> bool:
    if not is_edge_camera(camera):
        return False
    return EDGE_CAPABILITY_COLLISION in get_edge_capabilities(camera)


def apply_camera_mode_fields(record: dict, raw: dict) -> dict:
    mode = normalize_inference_mode(raw.get("inference_mode", record.get("inference_mode")))
    if mode == INFERENCE_MODE_CENTRAL:
        record.pop("inference_mode", None)
        record.pop("edge_capabilities", None)
        return record
    record["inference_mode"] = mode
    caps = normalize_edge_capabilities(raw.get("edge_capabilities", record.get("edge_capabilities")))
    if not caps:
        caps = [EDGE_CAPABILITY_POSE]
    record["edge_capabilities"] = caps
    return record
