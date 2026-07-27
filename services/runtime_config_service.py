"""全局运行时配置（持久化到 localdata/runtime_config.json）。"""

from __future__ import annotations

import json
import os
from typing import Any

from services.inference_backends.model_registry import (
    ALLOWED_PRESET_IDS,
    DEFAULT_DET_VARIANT,
    DEFAULT_PRESET_ID,
    normalize_backend_setting,
    normalize_det_setting,
)

DEFAULT_PATH = os.environ.get("RUNTIME_CONFIG_FILE", "localdata/runtime_config.json")

# 现场暴露项（与 ROADMAP 一致）
PUBLIC_KEYS = {
    "models.backend": ("models", "backend", str),
    "models.det": ("models", "det", str),
    "inference.frame_rate": ("inference", "frame_rate", int),
    "inference.height": ("inference", "height", int),
    "inference.pose_frame_interval": ("inference", "pose_frame_interval", int),
    "inference.alarm_min_consecutive_frames": ("inference", "alarm_min_consecutive_frames", int),
    "inference.alarm_cooldown_frames": ("inference", "alarm_cooldown_frames", int),
    "collision_prefilter.enabled": ("collision_prefilter", "enabled", bool),
    "collision_prefilter.speed_feature": ("collision_prefilter", "speed_feature", str),
    "collision_prefilter.speed_threshold": ("collision_prefilter", "speed_threshold", float),
    "collision_prefilter.arm_torso_min": ("collision_prefilter", "arm_torso_min", float),
    "collision_prefilter.elbow_min": ("collision_prefilter", "elbow_min", float),
    "collision_prefilter.wrist_elevation_min": ("collision_prefilter", "wrist_elevation_min", float),
    "collision_prefilter.stance_feature": ("collision_prefilter", "stance_feature", str),
    "collision_prefilter.stance_threshold": ("collision_prefilter", "stance_threshold", float),
    "collision_prefilter.max_pose_gap_sec": ("collision_prefilter", "max_pose_gap_sec", float),
    "debug-info.enabled": ("debug-info", "enabled", bool),
    "pipeline_log.enabled": ("pipeline_log", "enabled", bool),
    "pipeline_log.file_enabled": ("pipeline_log", "file_enabled", bool),
    "pipeline_log.dir": ("pipeline_log", "dir", str),
    "pipeline_log.sample": ("pipeline_log", "sample", int),
    "pipeline_log.stdout": ("pipeline_log", "stdout", bool),
    "pipeline_log.max_bytes": ("pipeline_log", "max_bytes", int),
    "pipeline_log.backup_count": ("pipeline_log", "backup_count", int),
}

# 仅全局设置页暴露，event-worker 读取；不支持按摄像头覆盖
GLOBAL_ONLY_KEYS = frozenset(
    {
        "inference.alarm_min_consecutive_frames",
        "inference.alarm_cooldown_frames",
        "collision_prefilter.enabled",
        "collision_prefilter.speed_feature",
        "collision_prefilter.speed_threshold",
        "collision_prefilter.arm_torso_min",
        "collision_prefilter.elbow_min",
        "collision_prefilter.wrist_elevation_min",
        "collision_prefilter.stance_feature",
        "collision_prefilter.stance_threshold",
        "collision_prefilter.max_pose_gap_sec",
        "pipeline_log.file_enabled",
        "pipeline_log.dir",
        "pipeline_log.sample",
        "pipeline_log.stdout",
        "pipeline_log.max_bytes",
        "pipeline_log.backup_count",
    }
)

# 单路摄像头可覆盖的全局项（不含 source.stream_url，流地址用摄像头 url 字段）
CAMERA_OVERRIDE_KEYS = {
    k: PUBLIC_KEYS[k]
    for k in (
        "models.backend",
        "models.det",
        "inference.frame_rate",
        "inference.height",
        "inference.pose_frame_interval",
        "debug-info.enabled",
        "pipeline_log.enabled",
    )
}

def _load_json(path: str) -> dict:
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _deep_get(cfg: dict, section: str, key: str, default: Any) -> Any:
    sec = cfg.get(section)
    if not isinstance(sec, dict):
        return default
    return sec.get(key, default)


def _deep_set(cfg: dict, section: str, key: str, value: Any) -> None:
    if section not in cfg or not isinstance(cfg[section], dict):
        cfg[section] = {}
    cfg[section][key] = value


def _normalize_backend(raw: Any) -> str:
    val = str(raw or "").strip().lower()
    if not val:
        raise ValueError("backend is required")
    return normalize_backend_setting(val)


def _coerce_alarm_min(raw: Any) -> int:
    val = int(raw)
    if val < 1:
        raise ValueError("must be >= 1")
    return val


def _coerce_alarm_cooldown(raw: Any) -> int:
    val = int(raw)
    if val < 0:
        raise ValueError("must be >= 0")
    return val


def _resolve_alarm_cooldown(cfg: dict) -> int:
    infer = cfg.get("inference")
    if isinstance(infer, dict) and "alarm_cooldown_frames" in infer:
        return max(0, int(infer["alarm_cooldown_frames"]))
    return 12


def _default_collision_prefilter_section() -> dict[str, Any]:
    return {
        "enabled": False,
        "speed_feature": "ankle_max_speed_norm",
        "speed_threshold": 0.081770,
        "arm_torso_min": 90.0,
        "elbow_min": 150.0,
        "wrist_elevation_min": 60.0,
        "stance_feature": "shoulder_hip_knee_angle_min",
        "stance_threshold": 140.0,
        "max_pose_gap_sec": 0.0,
    }


def get_collision_prefilter_section(app_config: dict | None = None, path: str = DEFAULT_PATH) -> dict:
    """app_config 与 runtime 覆盖合并（供 event-worker 读取碰撞前置门控）。"""
    base = dict(_default_collision_prefilter_section())
    app_sec = (app_config or {}).get("collision_prefilter")
    if isinstance(app_sec, dict):
        base.update(app_sec)
    overlay = _load_json(path)
    overlay_sec = overlay.get("collision_prefilter")
    if isinstance(overlay_sec, dict):
        base.update(overlay_sec)
    return base


def get_merged_inference_section(app_config: dict | None = None, path: str = DEFAULT_PATH) -> dict:
    """app_config.inference 与 runtime 覆盖合并（供 event-worker 读取告警门控）。"""
    base_infer = dict((app_config or {}).get("inference") or {})
    overlay = _load_json(path)
    overlay_infer = overlay.get("inference")
    if isinstance(overlay_infer, dict):
        base_infer.update(overlay_infer)
    return base_infer


def _default_pipeline_log_section() -> dict:
    return {
        "enabled": False,
        "file_enabled": False,
        "dir": "localdata/logs/pipeline",
        "sample": 30,
        "stdout": True,
        "max_bytes": 52_428_800,
        "backup_count": 5,
    }


def effective_pipeline_log_enabled(
    app_config: dict | None,
    camera: dict | None,
    path: str = DEFAULT_PATH,
) -> bool:
    """该路是否输出 [PIPELINE]：摄像头 settings 显式配置优先，否则继承全局默认（默认关）。"""
    section = get_pipeline_log_section(app_config, path)
    global_default = bool(section.get("enabled"))
    overrides = normalize_camera_settings((camera or {}).get("settings"))
    if "pipeline_log.enabled" in overrides:
        return bool(overrides["pipeline_log.enabled"])
    return global_default


def get_pipeline_log_section(app_config: dict | None = None, path: str = DEFAULT_PATH) -> dict:
    """app_config.pipeline_log 与 runtime 覆盖合并（供 infer / event-worker 读取）。"""
    base = dict(_default_pipeline_log_section())
    app_sec = (app_config or {}).get("pipeline_log")
    if isinstance(app_sec, dict):
        base.update(app_sec)
    overlay = _load_json(path)
    overlay_sec = overlay.get("pipeline_log")
    if isinstance(overlay_sec, dict):
        base.update(overlay_sec)
    try:
        base["sample"] = max(1, int(base.get("sample") or 30))
    except (TypeError, ValueError):
        base["sample"] = 30
    base["dir"] = str(base.get("dir") or "localdata/logs/pipeline").strip() or "localdata/logs/pipeline"
    base["enabled"] = bool(base.get("enabled"))
    base["file_enabled"] = bool(base.get("file_enabled"))
    base["stdout"] = bool(base.get("stdout", True))
    try:
        base["max_bytes"] = max(1024, int(base.get("max_bytes") or 52_428_800))
    except (TypeError, ValueError):
        base["max_bytes"] = 52_428_800
    try:
        base["backup_count"] = max(0, int(base.get("backup_count") if base.get("backup_count") is not None else 5))
    except (TypeError, ValueError):
        base["backup_count"] = 5
    return base


def _coerce_setting_value(pub_key: str, raw: Any, typ: type) -> Any:
    if pub_key == "models.backend":
        return _normalize_backend(raw)
    if pub_key == "models.det":
        return normalize_det_setting(str(raw))
    if pub_key == "inference.alarm_min_consecutive_frames":
        return _coerce_alarm_min(raw)
    if pub_key == "inference.alarm_cooldown_frames":
        return _coerce_alarm_cooldown(raw)
    if pub_key == "collision_prefilter.max_pose_gap_sec":
        return max(0.0, float(raw))
    if pub_key.startswith("collision_prefilter.") and typ is float:
        return float(raw)
    if typ is bool:
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            return raw.lower() in ("1", "true", "yes", "on")
        return bool(raw)
    if typ is int:
        val = int(raw)
        if val <= 0:
            raise ValueError("must be positive")
        return val
    return str(raw).strip()


def normalize_camera_settings(raw: dict | None, *, strict: bool = False) -> dict:
    """仅保留合法的摄像头级覆盖项。strict=True 时非法值抛错（供保存接口返回明确错误）。"""
    if not isinstance(raw, dict):
        return {}
    out = {}
    errors: list[str] = []
    for pub_key, (_, _, typ) in CAMERA_OVERRIDE_KEYS.items():
        if pub_key not in raw:
            continue
        val = raw[pub_key]
        if val is None or val == "":
            continue
        try:
            out[pub_key] = _coerce_setting_value(pub_key, val, typ)
        except (TypeError, ValueError) as exc:
            if strict:
                errors.append(f"{pub_key}: {exc}")
            continue
    if strict and errors:
        raise ValueError("; ".join(errors))
    return out


def get_public_settings(app_config: dict | None, path: str = DEFAULT_PATH) -> dict:
    base = app_config if isinstance(app_config, dict) else {}
    overlay = _load_json(path)
    merged = json.loads(json.dumps(base)) if base else {}
    for sec, key in [(s, k) for s, k, _ in PUBLIC_KEYS.values()]:
        if sec in overlay and isinstance(overlay[sec], dict) and key in overlay[sec]:
            _deep_set(merged, sec, key, overlay[sec][key])

    backend_raw = str(
        _deep_get(merged, "models", "backend", DEFAULT_PRESET_ID) or DEFAULT_PRESET_ID
    ).strip().lower()
    try:
        backend = normalize_backend_setting(backend_raw)
    except ValueError:
        backend = DEFAULT_PRESET_ID

    det_raw = str(_deep_get(merged, "models", "det", DEFAULT_DET_VARIANT) or DEFAULT_DET_VARIANT).strip().lower()
    try:
        det = normalize_det_setting(det_raw)
    except ValueError:
        det = DEFAULT_DET_VARIANT

    return {
        "status": "success",
        "items": {
            "models.backend": backend,
            "models.det": det,
            "inference.frame_rate": _deep_get(merged, "inference", "frame_rate", 15),
            "inference.height": _deep_get(merged, "inference", "height", 480),
            "inference.pose_frame_interval": _deep_get(merged, "inference", "pose_frame_interval", 3),
            "inference.alarm_min_consecutive_frames": max(
                1,
                int(_deep_get(merged, "inference", "alarm_min_consecutive_frames", 3) or 3),
            ),
            "inference.alarm_cooldown_frames": _resolve_alarm_cooldown(merged),
            "collision_prefilter.enabled": bool(
                _deep_get(merged, "collision_prefilter", "enabled", False)
            ),
            "collision_prefilter.speed_feature": str(
                _deep_get(merged, "collision_prefilter", "speed_feature", "ankle_max_speed_norm")
                or "ankle_max_speed_norm"
            ),
            "collision_prefilter.speed_threshold": float(
                _deep_get(merged, "collision_prefilter", "speed_threshold", 0.081770)
            ),
            "collision_prefilter.arm_torso_min": float(
                _deep_get(merged, "collision_prefilter", "arm_torso_min", 90.0)
            ),
            "collision_prefilter.elbow_min": float(
                _deep_get(merged, "collision_prefilter", "elbow_min", 150.0)
            ),
            "collision_prefilter.wrist_elevation_min": float(
                _deep_get(merged, "collision_prefilter", "wrist_elevation_min", 60.0)
            ),
            "collision_prefilter.stance_feature": str(
                _deep_get(
                    merged,
                    "collision_prefilter",
                    "stance_feature",
                    "shoulder_hip_knee_angle_min",
                )
                or "shoulder_hip_knee_angle_min"
            ),
            "collision_prefilter.stance_threshold": float(
                _deep_get(merged, "collision_prefilter", "stance_threshold", 140.0)
            ),
            "collision_prefilter.max_pose_gap_sec": float(
                _deep_get(merged, "collision_prefilter", "max_pose_gap_sec", 0.0) or 0.0
            ),
            "debug-info.enabled": bool(_deep_get(merged, "debug-info", "enabled", False)),
            "pipeline_log.enabled": bool(_deep_get(merged, "pipeline_log", "enabled", False)),
            "pipeline_log.file_enabled": bool(_deep_get(merged, "pipeline_log", "file_enabled", False)),
            "pipeline_log.dir": str(
                _deep_get(merged, "pipeline_log", "dir", "localdata/logs/pipeline")
                or "localdata/logs/pipeline"
            ),
            "pipeline_log.sample": max(1, int(_deep_get(merged, "pipeline_log", "sample", 30) or 30)),
            "pipeline_log.stdout": bool(_deep_get(merged, "pipeline_log", "stdout", True)),
            "pipeline_log.max_bytes": max(
                1024, int(_deep_get(merged, "pipeline_log", "max_bytes", 52_428_800) or 52_428_800)
            ),
            "pipeline_log.backup_count": max(
                0, int(_deep_get(merged, "pipeline_log", "backup_count", 5) if _deep_get(merged, "pipeline_log", "backup_count", 5) is not None else 5)
            ),
        },
    }


def patch_public_settings(updates: dict, path: str = DEFAULT_PATH) -> dict:
    overlay = _load_json(path)
    applied = {}
    errors = []
    for pub_key, (section, key, typ) in PUBLIC_KEYS.items():
        if pub_key not in updates:
            continue
        raw = updates[pub_key]
        try:
            if pub_key == "models.backend":
                val = _normalize_backend(raw)
            elif pub_key == "models.det":
                val = normalize_det_setting(str(raw))
            elif pub_key == "inference.alarm_min_consecutive_frames":
                val = _coerce_alarm_min(raw)
            elif pub_key == "inference.alarm_cooldown_frames":
                val = _coerce_alarm_cooldown(raw)
            elif pub_key == "pipeline_log.max_bytes":
                val = max(1024, int(raw))
            elif pub_key == "pipeline_log.backup_count":
                val = max(0, int(raw))
            elif pub_key == "collision_prefilter.max_pose_gap_sec":
                val = max(0.0, float(raw))
            elif pub_key.startswith("collision_prefilter.") and typ is float:
                val = float(raw)
            elif typ is bool:
                val = bool(raw) if not isinstance(raw, str) else raw.lower() in ("1", "true", "yes", "on")
            elif typ is int:
                val = int(raw)
                if val <= 0:
                    raise ValueError("must be positive")
            else:
                val = str(raw).strip()
            _deep_set(overlay, section, key, val)
            applied[pub_key] = val
        except (TypeError, ValueError) as e:
            errors.append(f"{pub_key}: {e}")
    if errors:
        return {"status": "error", "error": "; ".join(errors)}
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(overlay, f, ensure_ascii=False, indent=2)
    return {"status": "success", "applied": applied}


def get_effective_settings(
    app_config: dict | None = None,
    camera: dict | None = None,
    path: str = DEFAULT_PATH,
) -> dict[str, Any]:
    """全局默认 + 摄像头 settings 覆盖。"""
    base_cfg = app_config if isinstance(app_config, dict) else {}
    items = dict(get_public_settings(base_cfg, path=path).get("items") or {})
    overrides = normalize_camera_settings((camera or {}).get("settings"))
    for key, val in overrides.items():
        items[key] = val
    return items


def get_camera_settings_payload(
    app_config: dict | None,
    camera: dict,
    path: str = DEFAULT_PATH,
) -> dict:
    """返回摄像头 settings 与合并后的 effective_settings。"""
    overrides = normalize_camera_settings(camera.get("settings"))
    effective = get_effective_settings(app_config, {**camera, "settings": overrides}, path=path)
    return {
        "settings": overrides,
        "effective_settings": {k: effective[k] for k in CAMERA_OVERRIDE_KEYS if k in effective},
        "global_defaults": {
            k: get_public_settings(app_config, path=path)["items"].get(k)
            for k in CAMERA_OVERRIDE_KEYS
        },
    }
