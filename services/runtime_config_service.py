"""全局运行时配置（持久化到 localdata/runtime_config.json）。"""

from __future__ import annotations

import json
import os
from typing import Any

from services.inference_backends.model_registry import (
    ALLOWED_PRESET_IDS,
    DEFAULT_PRESET_ID,
    normalize_backend_setting,
)

DEFAULT_PATH = os.environ.get("RUNTIME_CONFIG_FILE", "localdata/runtime_config.json")

# 现场暴露项（与 ROADMAP 一致）
PUBLIC_KEYS = {
    "models.backend": ("models", "backend", str),
    "inference.frame_rate": ("inference", "frame_rate", int),
    "inference.height": ("inference", "height", int),
    "inference.pose_frame_interval": ("inference", "pose_frame_interval", int),
    "debug-info.enabled": ("debug-info", "enabled", bool),
}

# 碰撞检测（Event Worker 全局，写入 runtime_config.inference.collision）
COLLISION_PUBLIC_KEYS: dict[str, tuple[str, type]] = {
    "inference.collision.min_consecutive_frames": ("min_consecutive_frames", int),
    "inference.collision.cooldown_frames": ("cooldown_frames", int),
    "inference.collision.window_frames": ("window_frames", int),
    "inference.collision.wrist_conf": ("wrist_conf", float),
    "inference.collision.elbow_conf": ("elbow_conf", float),
    "inference.collision.forearm_extend_ratio": ("forearm_extend_ratio", float),
    "inference.collision.boundary_margin_ratio": ("boundary_margin_ratio", float),
    "inference.collision.boundary_margin_min_px": ("boundary_margin_min_px", float),
    "inference.collision.track_max_match_dist": ("track_max_match_dist", float),
    "inference.collision.track_stale_sec": ("track_stale_sec", float),
    "inference.collision.per_track_gating": ("per_track_gating", bool),
}

_COLLISION_BOUNDS: dict[str, tuple[float, float]] = {
    "min_consecutive_frames": (1, 30),
    "cooldown_frames": (1, 600),
    "window_frames": (1, 60),
    "wrist_conf": (0.1, 1.0),
    "elbow_conf": (0.1, 1.0),
    "forearm_extend_ratio": (0.0, 1.0),
    "boundary_margin_ratio": (0.0, 0.5),
    "boundary_margin_min_px": (0.0, 50.0),
    "track_max_match_dist": (50.0, 500.0),
    "track_stale_sec": (0.3, 10.0),
}

# 单路摄像头可覆盖的全局项（不含 source.stream_url，流地址用摄像头 url 字段）
CAMERA_OVERRIDE_KEYS = {
    k: PUBLIC_KEYS[k]
    for k in (
        "models.backend",
        "inference.frame_rate",
        "inference.height",
        "inference.pose_frame_interval",
        "debug-info.enabled",
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


def _collision_overlay_get(overlay: dict) -> dict:
    infer = overlay.get("inference")
    if not isinstance(infer, dict):
        return {}
    coll = infer.get("collision")
    return coll if isinstance(coll, dict) else {}


def _collision_overlay_set(overlay: dict, key: str, value: Any) -> None:
    if "inference" not in overlay or not isinstance(overlay["inference"], dict):
        overlay["inference"] = {}
    if "collision" not in overlay["inference"] or not isinstance(overlay["inference"]["collision"], dict):
        overlay["inference"]["collision"] = {}
    overlay["inference"]["collision"][key] = value


_COLLISION_DEFAULTS: dict[str, int | float | bool] = {
    "min_consecutive_frames": 3,
    "cooldown_frames": 6,
    "window_frames": 6,
    "wrist_conf": 0.45,
    "elbow_conf": 0.4,
    "forearm_extend_ratio": 0.2,
    "boundary_margin_ratio": 0.04,
    "boundary_margin_min_px": 3.0,
    "track_max_match_dist": 220.0,
    "track_stale_sec": 1.2,
    "per_track_gating": True,
}


def _collision_defaults(app_config: dict | None) -> dict:
    infer = (app_config or {}).get("inference") if isinstance(app_config, dict) else {}
    if not isinstance(infer, dict):
        infer = {}
    out = dict(_COLLISION_DEFAULTS)
    coll = infer.get("collision")
    if isinstance(coll, dict):
        for key in out:
            if key in coll and coll[key] is not None:
                out[key] = coll[key]
    if infer.get("alarm_min_consecutive_frames") is not None:
        out["min_consecutive_frames"] = int(infer["alarm_min_consecutive_frames"])
    if infer.get("alarm_cooldown_frames") is not None:
        out["cooldown_frames"] = int(infer["alarm_cooldown_frames"])
    out["window_frames"] = max(int(out["min_consecutive_frames"]), int(out["window_frames"]))
    return out


def get_merged_inference_config(app_config: dict | None, path: str = DEFAULT_PATH) -> dict:
    """app_config.inference + runtime overlay（含 collision）。"""
    base = app_config if isinstance(app_config, dict) else {}
    infer = base.get("inference") if isinstance(base.get("inference"), dict) else {}
    merged = json.loads(json.dumps(infer)) if infer else {}
    overlay = _load_json(path)
    overlay_inf = overlay.get("inference") if isinstance(overlay.get("inference"), dict) else {}
    for key in ("frame_rate", "height", "pose_frame_interval", "alarm_min_consecutive_frames", "alarm_cooldown_frames"):
        if key in overlay_inf:
            merged[key] = overlay_inf[key]
    defaults = _collision_defaults(base)
    merged["collision"] = {**defaults, **_collision_overlay_get(overlay)}
    return merged


def runtime_config_mtime(path: str = DEFAULT_PATH) -> float:
    try:
        return os.path.getmtime(path) if os.path.isfile(path) else 0.0
    except OSError:
        return 0.0


def _normalize_backend(raw: Any) -> str:
    val = str(raw or "").strip().lower()
    if not val:
        raise ValueError("backend is required")
    return normalize_backend_setting(val)


def _coerce_setting_value(pub_key: str, raw: Any, typ: type) -> Any:
    if pub_key == "models.backend":
        return _normalize_backend(raw)
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


def _coerce_collision_value(key: str, raw: Any, typ: type) -> Any:
    if typ is bool:
        if isinstance(raw, bool):
            val = raw
        elif isinstance(raw, str):
            val = raw.strip().lower() in ("1", "true", "yes", "on")
        else:
            val = bool(raw)
    elif typ is int:
        val = int(raw)
    elif typ is float:
        val = float(raw)
    else:
        raise ValueError("unsupported type")
    bounds = _COLLISION_BOUNDS.get(key)
    if bounds is not None:
        lo, hi = bounds
        if val < lo or val > hi:
            raise ValueError(f"must be between {lo} and {hi}")
    if typ is int and val <= 0:
        raise ValueError("must be positive")
    return val


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

    collision = _collision_defaults(base)
    collision.update(_collision_overlay_get(overlay))

    items = {
        "models.backend": backend,
        "inference.frame_rate": _deep_get(merged, "inference", "frame_rate", 15),
        "inference.height": _deep_get(merged, "inference", "height", 480),
        "inference.pose_frame_interval": _deep_get(merged, "inference", "pose_frame_interval", 3),
        "debug-info.enabled": bool(_deep_get(merged, "debug-info", "enabled", False)),
    }
    for pub_key, (coll_key, _) in COLLISION_PUBLIC_KEYS.items():
        items[pub_key] = collision.get(coll_key)

    return {
        "status": "success",
        "items": items,
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
            elif typ is bool:
                val = bool(raw) if not isinstance(raw, str) else raw.lower() in ("1", "true", "yes", "on")
            elif typ is int:
                val = int(raw)
                if val <= 0 and pub_key != "debug-info.enabled":
                    raise ValueError("must be positive")
            else:
                val = str(raw).strip()
            _deep_set(overlay, section, key, val)
            applied[pub_key] = val
        except (TypeError, ValueError) as e:
            errors.append(f"{pub_key}: {e}")
    for pub_key, (coll_key, typ) in COLLISION_PUBLIC_KEYS.items():
        if pub_key not in updates:
            continue
        raw = updates[pub_key]
        try:
            val = _coerce_collision_value(coll_key, raw, typ)
            _collision_overlay_set(overlay, coll_key, val)
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
