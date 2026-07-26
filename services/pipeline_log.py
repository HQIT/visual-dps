"""流水线阶段日志：logging 统一管理，配置来自 app_config / runtime_config。"""

from __future__ import annotations

import logging
import os
import sys
import threading
from typing import Any

from services.runtime_config_service import DEFAULT_PATH, get_pipeline_log_section
from services.wall_clock import wall_time_str

_LOGGER_NAME = "visual_dps.pipeline"
_lock = threading.Lock()
_configured = False
_config_loaded = False
_role = "pipeline"
_settings: dict[str, Any] = {
    "enabled": False,
    "file_enabled": False,
    "dir": "localdata/logs/pipeline",
    "sample": 30,
    "stdout": True,
}

# 每帧阶段（采样输出，避免刷屏）
_FRAME_STAGES = frozenset(
    {
        "rtsp_frame",
        "infer_pose_done",
        "pose_published",
        "worker_received",
        "worker_done",
        "event_published",
        "callback_enqueued",
    }
)


def _truthy(val: str | None) -> bool:
    return str(val or "").strip().lower() in ("1", "true", "yes", "on")


def _resolve_bool_env(env_key: str, config_val: Any, default: bool = False) -> bool:
    raw = os.environ.get(env_key, "").strip()
    if raw:
        return _truthy(raw)
    if config_val is not None:
        return bool(config_val)
    return default


def _resolve_str_env(env_key: str, config_val: Any, default: str) -> str:
    raw = os.environ.get(env_key, "").strip()
    if raw:
        return raw
    text = str(config_val or "").strip()
    return text or default


def _resolve_int_env(env_key: str, config_val: Any, default: int) -> int:
    raw = os.environ.get(env_key, "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            return default
    try:
        return max(1, int(config_val))
    except (TypeError, ValueError):
        return default


def apply_pipeline_log_config(
    app_config: dict | None = None,
    runtime_path: str = DEFAULT_PATH,
) -> dict[str, Any]:
    """从 app_config + runtime_config 加载流水线日志配置；环境变量可覆盖。"""
    global _configured, _config_loaded, _settings

    section = get_pipeline_log_section(app_config, runtime_path)
    _settings = {
        "enabled": _resolve_bool_env("PIPELINE_LOG", section.get("enabled"), False),
        "file_enabled": _resolve_bool_env("PIPELINE_LOG_FILE", section.get("file_enabled"), False),
        "dir": _resolve_str_env("PIPELINE_LOG_DIR", section.get("dir"), "localdata/logs/pipeline"),
        "sample": _resolve_int_env("PIPELINE_LOG_SAMPLE", section.get("sample"), 30),
        "stdout": _resolve_bool_env("PIPELINE_LOG_STDOUT", section.get("stdout"), True),
    }
    _config_loaded = True
    _configured = False

    logger = _get_logger()
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)

    return dict(_settings)


def pipeline_log_enabled() -> bool:
    if _config_loaded:
        return bool(_settings.get("enabled"))
    return _truthy(os.environ.get("PIPELINE_LOG"))


def pipeline_log_file_enabled() -> bool:
    if not pipeline_log_enabled():
        return False
    if _config_loaded:
        return bool(_settings.get("file_enabled"))
    return _truthy(os.environ.get("PIPELINE_LOG_FILE"))


def pipeline_log_stdout_enabled() -> bool:
    if not pipeline_log_enabled():
        return False
    if _config_loaded:
        return bool(_settings.get("stdout", True))
    raw = os.environ.get("PIPELINE_LOG_STDOUT", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def pipeline_log_dir() -> str:
    if _config_loaded:
        return str(_settings.get("dir") or "localdata/logs/pipeline")
    raw = os.environ.get("PIPELINE_LOG_DIR", "localdata/logs/pipeline").strip()
    return raw or "localdata/logs/pipeline"


def pipeline_log_sample_every() -> int:
    if _config_loaded:
        return max(1, int(_settings.get("sample") or 30))
    raw = os.environ.get("PIPELINE_LOG_SAMPLE", "30").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 30


def sample_hit(frame_idx: int) -> bool:
    if not pipeline_log_enabled():
        return False
    fi = int(frame_idx or 0)
    if fi <= 0:
        return True
    return (fi % pipeline_log_sample_every()) == 0


def _get_logger() -> logging.Logger:
    return logging.getLogger(_LOGGER_NAME)


def configure_pipeline_logger(*, role: str = "pipeline") -> None:
    """进程入口调用一次，按已加载配置注册 logging handler。"""
    global _configured, _role
    if not pipeline_log_enabled():
        return

    with _lock:
        if _configured:
            return

        _role = str(role or "pipeline").strip() or "pipeline"
        logger = _get_logger()
        logger.setLevel(logging.INFO)
        logger.propagate = False
        formatter = logging.Formatter("%(message)s")

        if pipeline_log_stdout_enabled():
            stream_handler = logging.StreamHandler(sys.stdout)
            stream_handler.setFormatter(formatter)
            logger.addHandler(stream_handler)

        if pipeline_log_file_enabled():
            log_dir = pipeline_log_dir()
            os.makedirs(log_dir, exist_ok=True)
            file_path = os.path.join(log_dir, f"{_role}.log")
            file_handler = logging.FileHandler(file_path, encoding="utf-8")
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)

        if not logger.handlers:
            stream_handler = logging.StreamHandler(sys.stdout)
            stream_handler.setFormatter(formatter)
            logger.addHandler(stream_handler)

        _configured = True


def pipeline_log_file_path() -> str | None:
    if not pipeline_log_file_enabled():
        return None
    return os.path.join(pipeline_log_dir(), f"{_role}.log")


def _fmt_value(val: Any) -> str:
    if val is None:
        return "—"
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, float):
        return f"{val:.3f}".rstrip("0").rstrip(".")
    if isinstance(val, dict):
        if not val:
            return "{}"
        parts = [f"{k}={_fmt_value(v)}" for k, v in sorted(val.items())]
        return "{" + ",".join(parts) + "}"
    return str(val)


def _format_line(stage: str, *, camera_id: str, frame_idx: int, **fields: Any) -> str:
    ordered = [
        ("time", wall_time_str()),
        ("stage", stage),
        ("camera", camera_id or "—"),
        ("frame", frame_idx),
    ]
    for key in sorted(fields):
        ordered.append((key, fields[key]))
    body = " ".join(f"{key}={_fmt_value(value)}" for key, value in ordered)
    return f"[PIPELINE] {body}"


def log_pipeline_stage(
    stage: str,
    *,
    camera_id: str = "",
    frame_idx: int = 0,
    sample: bool = True,
    **fields: Any,
) -> None:
    """记录流水线阶段；帧级 stage 默认按 sample 配置采样。"""
    if not pipeline_log_enabled():
        return
    if sample and stage in _FRAME_STAGES and not sample_hit(frame_idx):
        return

    configure_pipeline_logger(role=_role)
    line = _format_line(stage, camera_id=camera_id, frame_idx=frame_idx, **fields)
    _get_logger().info(line)


def log_pipeline_info(message: str) -> None:
    """非采样信息（启动、配置等）。"""
    if not pipeline_log_enabled():
        return
    configure_pipeline_logger(role=_role)
    _get_logger().info(f"[PIPELINE] {message}")
