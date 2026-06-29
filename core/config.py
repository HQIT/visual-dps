"""应用配置的严格加载与校验模块。"""

import json
import os

CONFIG_FILE = "app_config.json"

REQUIRED_CONFIG_KEYS = {
    "paths": [
        "templates_dir",
        "index_html",
        "annotation_html",
        "base_localdata_dir",
        "upload_dir",
        "upload_480p_dir",
        "json_dir",
        "default_json_file",
        "counter_file",
        "camera_ips_file",
        "last_frame_file",
    ],
    "video": ["transcode_height", "capture_height"],
    "models": ["backend"],
    "inference": [
        "frame_rate",
        "height",
        "pose_frame_interval",
        "stream_buffer_size",
        "preview_max_width",
        "preview_jpeg_quality",
    ],
    "server": ["host", "port"],
}


def _normalize_local_path(path_value: str) -> str:
    if path_value.startswith("http://") or path_value.startswith("https://"):
        return path_value
    return os.path.normpath(path_value)


def _validate_positive_int(cfg: dict, section: str, key: str, errors: list):
    try:
        value = int(cfg[section][key])
        if value <= 0:
            errors.append(f"字段必须大于 0: {section}.{key}")
    except Exception:
        errors.append(f"字段必须是整数: {section}.{key}")


def _validate_config_or_raise(cfg: dict, config_file: str):
    errors = []

    for section, keys in REQUIRED_CONFIG_KEYS.items():
        section_data = cfg.get(section)
        if not isinstance(section_data, dict):
            errors.append(f"缺少对象字段: {section}")
            continue

        for key in keys:
            if key not in section_data:
                errors.append(f"缺少字段: {section}.{key}")
                continue

            value = section_data[key]
            if isinstance(value, str) and not value.strip():
                errors.append(f"字段不能为空: {section}.{key}")

    for section, key in [
        ("video", "transcode_height"),
        ("video", "capture_height"),
        ("inference", "height"),
        ("inference", "frame_rate"),
        ("inference", "pose_frame_interval"),
        ("inference", "stream_buffer_size"),
        ("inference", "preview_max_width"),
    ]:
        if section in cfg and isinstance(cfg.get(section), dict) and key in cfg[section]:
            _validate_positive_int(cfg, section, key, errors)

    try:
        jpeg_quality = int(cfg["inference"]["preview_jpeg_quality"])
        if not (1 <= jpeg_quality <= 100):
            errors.append("字段范围无效(1-100): inference.preview_jpeg_quality")
    except Exception:
        errors.append("字段必须是整数: inference.preview_jpeg_quality")

    try:
        port = int(cfg["server"]["port"])
        if not (1 <= port <= 65535):
            errors.append("字段范围无效(1-65535): server.port")
    except Exception:
        errors.append("字段必须是整数: server.port")

    models_cfg = cfg.get("models")
    if isinstance(models_cfg, dict):
        backend = str(models_cfg.get("backend", "rtmpose_t")).strip().lower()
        try:
            from services.inference_backends.model_registry import (
                normalize_backend_setting,
                normalize_det_setting,
            )
        except ImportError:
            pass
        else:
            try:
                normalize_backend_setting(backend)
            except ValueError:
                errors.append(f"models.backend 无效: {backend}")
            try:
                normalize_det_setting(str(models_cfg.get("det", "nano")).strip().lower())
            except ValueError:
                errors.append(f"models.det 无效: {models_cfg.get('det', 'nano')}")

    if errors:
        msg = [f"配置文件校验失败: {config_file}"]
        msg.extend([f"- {e}" for e in errors])
        raise SystemExit("\n".join(msg))


def try_load_app_config(config_file: str = CONFIG_FILE) -> dict | None:
    """加载配置；文件缺失或校验失败时返回 None（API 路由用，避免 SystemExit）。"""
    if not os.path.isfile(config_file):
        return None
    try:
        return load_app_config(config_file)
    except SystemExit:
        return None


def load_app_config(config_file: str = CONFIG_FILE) -> dict:
    if not os.path.exists(config_file):
        raise SystemExit(f"配置文件不存在: {config_file}，请先创建后再启动。")

    try:
        with open(config_file, "r", encoding="utf-8") as f:
            loaded = json.load(f)
    except json.JSONDecodeError as e:
        raise SystemExit(f"配置文件 JSON 格式错误: {config_file}，详情: {e}")
    except Exception as e:
        raise SystemExit(f"读取配置文件失败: {config_file}，详情: {e}")

    if not isinstance(loaded, dict):
        raise SystemExit(f"配置文件根节点必须是对象: {config_file}")

    _validate_config_or_raise(loaded, config_file)

    for k, v in loaded.get("paths", {}).items():
        if isinstance(v, str):
            loaded["paths"][k] = _normalize_local_path(v)

    for k, v in loaded.get("models", {}).items():
        if isinstance(v, str):
            loaded["models"][k] = _normalize_local_path(v)

    return loaded
