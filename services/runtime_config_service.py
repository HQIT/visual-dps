"""全局运行时配置（持久化到 localdata/runtime_config.json）。"""

from __future__ import annotations

import json
import os
from typing import Any

from services.inference_backends import (
    BACKEND_MMPose,
    BACKEND_RTMPOSE_ONNX,
    _ALIASES as _INFERENCE_BACKEND_ALIASES,
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

_BACKEND_ALIASES = dict(_INFERENCE_BACKEND_ALIASES)
_ALLOWED_BACKENDS = frozenset({BACKEND_MMPose, BACKEND_RTMPOSE_ONNX})


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
    normalized = _BACKEND_ALIASES.get(val, val)
    if normalized not in _ALLOWED_BACKENDS:
        raise ValueError(f"backend must be one of: {', '.join(sorted(_ALLOWED_BACKENDS))}")
    return normalized


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

    backend_raw = str(_deep_get(merged, "models", "backend", "mmpose") or "mmpose").strip().lower()
    backend = _BACKEND_ALIASES.get(backend_raw, backend_raw)
    if backend not in _ALLOWED_BACKENDS:
        backend = "mmpose"

    return {
        "status": "success",
        "items": {
            "models.backend": backend,
            "inference.frame_rate": _deep_get(merged, "inference", "frame_rate", 15),
            "inference.height": _deep_get(merged, "inference", "height", 480),
            "inference.pose_frame_interval": _deep_get(merged, "inference", "pose_frame_interval", 3),
            "debug-info.enabled": bool(_deep_get(merged, "debug-info", "enabled", False)),
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
