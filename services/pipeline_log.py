"""推理链路日志中枢：logging 统一管理，配置来自 app_config / runtime_config。"""

from __future__ import annotations

import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler
from typing import Any

from services.runtime_config_service import DEFAULT_PATH, get_pipeline_log_section
from services.wall_clock import wall_time_str

_LOGGER_PIPELINE = "visual_dps.pipeline"
_LOGGER_BOOT = "visual_dps.boot"
_LOGGER_INFERENCE = "visual_dps.inference"
_LOGGER_COLLISION = "visual_dps.collision"
_LOGGER_PREFILTER = "visual_dps.prefilter"
_LOGGER_CALLBACK = "visual_dps.callback"

_ALL_LOGGER_NAMES = (
    _LOGGER_BOOT,
    _LOGGER_INFERENCE,
    _LOGGER_PIPELINE,
    _LOGGER_COLLISION,
    _LOGGER_PREFILTER,
    _LOGGER_CALLBACK,
)

_lock = threading.Lock()
_configured = False
_config_loaded = False
_role = "pipeline"
_app_config: dict | None = None
_runtime_path = DEFAULT_PATH
_file_handler_attached = False
_active_file_config_key: tuple[str, str, int, int] | None = None
_callback_reporting_enabled = True

_settings: dict[str, Any] = {
    "enabled": False,
    "file_enabled": False,
    "dir": "localdata/logs/pipeline",
    "sample": 30,
    "stdout": True,
    "max_bytes": 52_428_800,
    "backup_count": 5,
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


def collision_log_enabled() -> bool:
    return _truthy(os.environ.get("COLLISION_LOG"))


def prefilter_log_enabled() -> bool:
    for key in ("PREFILTER_LOG", "COLLISION_LOG"):
        if _truthy(os.environ.get(key)):
            return True
    return False


def set_callback_reporting_enabled(enabled: bool) -> None:
    global _callback_reporting_enabled
    _callback_reporting_enabled = bool(enabled)


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


def _resolve_int_env(env_key: str, config_val: Any, default: int, *, minimum: int = 1) -> int:
    raw = os.environ.get(env_key, "").strip()
    if raw:
        try:
            return max(minimum, int(raw))
        except ValueError:
            return default
    try:
        return max(minimum, int(config_val))
    except (TypeError, ValueError):
        return default


def _load_settings_from_section(section: dict[str, Any]) -> dict[str, Any]:
    return {
        "enabled": _resolve_bool_env("PIPELINE_LOG", section.get("enabled"), False),
        "file_enabled": _resolve_bool_env("PIPELINE_LOG_FILE", section.get("file_enabled"), False),
        "dir": _resolve_str_env("PIPELINE_LOG_DIR", section.get("dir"), "localdata/logs/pipeline"),
        "sample": _resolve_int_env("PIPELINE_LOG_SAMPLE", section.get("sample"), 30),
        "stdout": _resolve_bool_env("PIPELINE_LOG_STDOUT", section.get("stdout"), True),
        "max_bytes": _resolve_int_env(
            "PIPELINE_LOG_MAX_BYTES",
            section.get("max_bytes"),
            52_428_800,
            minimum=1024,
        ),
        "backup_count": _resolve_int_env(
            "PIPELINE_LOG_BACKUP_COUNT",
            section.get("backup_count"),
            5,
            minimum=0,
        ),
    }


def _resolve_runtime_path(runtime_path: str | None = None) -> str:
    if runtime_path:
        return runtime_path
    return os.environ.get("RUNTIME_CONFIG_FILE", DEFAULT_PATH)


def apply_pipeline_log_config(
    app_config: dict | None = None,
    runtime_path: str | None = None,
) -> dict[str, Any]:
    """从 app_config + runtime_config 加载流水线日志配置；环境变量可覆盖。"""
    global _config_loaded, _configured, _settings, _app_config, _runtime_path

    path = _resolve_runtime_path(runtime_path)
    section = get_pipeline_log_section(app_config, path)
    _settings = _load_settings_from_section(section)
    _app_config = app_config
    _runtime_path = path
    _config_loaded = True
    _configured = False

    _clear_logger_handlers(_get_logger(_LOGGER_PIPELINE))

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


def _get_logger(name: str = _LOGGER_PIPELINE) -> logging.Logger:
    return logging.getLogger(name)


def get_boot_logger() -> logging.Logger:
    return _get_logger(_LOGGER_BOOT)


def get_inference_logger() -> logging.Logger:
    return _get_logger(_LOGGER_INFERENCE)


def get_collision_logger() -> logging.Logger:
    return _get_logger(_LOGGER_COLLISION)


def get_prefilter_logger() -> logging.Logger:
    return _get_logger(_LOGGER_PREFILTER)


def get_callback_logger() -> logging.Logger:
    return _get_logger(_LOGGER_CALLBACK)


def _message_formatter() -> logging.Formatter:
    return logging.Formatter("%(message)s")


def _clear_logger_handlers(logger: logging.Logger) -> None:
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)


def _clear_all_handlers() -> None:
    global _file_handler_attached
    for name in _ALL_LOGGER_NAMES:
        _clear_logger_handlers(_get_logger(name))
    _file_handler_attached = False


def _make_file_config_key() -> tuple[str, str, int, int]:
    return (
        pipeline_log_dir(),
        _role,
        int(_settings.get("max_bytes") or 52_428_800),
        int(_settings.get("backup_count") or 5),
    )


def _build_stdout_handler() -> logging.StreamHandler:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_message_formatter())
    return handler


def _build_file_handler() -> RotatingFileHandler:
    log_dir = pipeline_log_dir()
    os.makedirs(log_dir, exist_ok=True)
    file_path = os.path.join(log_dir, f"{_role}.log")
    handler = RotatingFileHandler(
        file_path,
        maxBytes=int(_settings.get("max_bytes") or 52_428_800),
        backupCount=int(_settings.get("backup_count") or 5),
        encoding="utf-8",
    )
    handler.setFormatter(_message_formatter())
    return handler


def _setup_logger(name: str, *, level: int, stdout: bool, file_handler: RotatingFileHandler | None) -> None:
    logger = _get_logger(name)
    logger.setLevel(level)
    logger.propagate = False
    if stdout:
        logger.addHandler(_build_stdout_handler())
    if file_handler is not None:
        logger.addHandler(file_handler)


def _configure_handlers(*, allow_file_rebuild: bool = True) -> None:
    global _file_handler_attached, _active_file_config_key

    _clear_all_handlers()

    shared_file: RotatingFileHandler | None = None
    want_file = bool(_settings.get("file_enabled"))

    if want_file and allow_file_rebuild:
        shared_file = _build_file_handler()
        _file_handler_attached = True
        _active_file_config_key = _make_file_config_key()

    # boot / inference：始终 stdout
    _setup_logger(_LOGGER_BOOT, level=logging.INFO, stdout=True, file_handler=shared_file)
    _setup_logger(_LOGGER_INFERENCE, level=logging.INFO, stdout=True, file_handler=shared_file)

    pipeline_stdout = pipeline_log_stdout_enabled() if pipeline_log_enabled() else False
    _setup_logger(
        _LOGGER_PIPELINE,
        level=logging.INFO if pipeline_log_enabled() else logging.CRITICAL + 1,
        stdout=pipeline_stdout,
        file_handler=shared_file if pipeline_log_enabled() and want_file else None,
    )

    _setup_logger(
        _LOGGER_COLLISION,
        level=logging.INFO if collision_log_enabled() else logging.CRITICAL + 1,
        stdout=collision_log_enabled(),
        file_handler=shared_file if collision_log_enabled() and want_file else None,
    )

    _setup_logger(
        _LOGGER_PREFILTER,
        level=logging.INFO if prefilter_log_enabled() else logging.CRITICAL + 1,
        stdout=prefilter_log_enabled(),
        file_handler=shared_file if prefilter_log_enabled() and want_file else None,
    )

    callback_active = _callback_reporting_enabled
    _setup_logger(
        _LOGGER_CALLBACK,
        level=logging.INFO if callback_active else logging.WARNING,
        stdout=True,
        file_handler=shared_file if callback_active and want_file else None,
    )


def configure_process_logging(*, role: str, app_config: dict | None = None) -> None:
    """进程入口调用：加载配置并注册全部 logger handler。"""
    global _configured, _role

    apply_pipeline_log_config(app_config)
    with _lock:
        _role = str(role or "pipeline").strip() or "pipeline"
        _configure_handlers(allow_file_rebuild=True)
        _configured = True


def configure_pipeline_logger(*, role: str = "pipeline") -> None:
    """兼容旧 API：等价于 configure_process_logging。"""
    configure_process_logging(role=role, app_config=_app_config)


def reload_process_logging(app_config: dict | None = None) -> None:
    """runtime_config 变更时热更新；文件路径/轮转参数变更时不重建 file handler。"""
    global _configured

    with _lock:
        old_key = _active_file_config_key

        if app_config is not None:
            apply_pipeline_log_config(app_config, _runtime_path)
        else:
            apply_pipeline_log_config(_app_config, _runtime_path)

        if not _configured:
            _configure_handlers(allow_file_rebuild=True)
            _configured = True
            return

        new_key = _make_file_config_key()
        want_file = bool(_settings.get("file_enabled"))
        file_changed = want_file and old_key is not None and old_key != new_key

        if file_changed:
            get_boot_logger().warning(
                "流水线日志文件路径或轮转参数已变更，需重启容器后生效；"
                f"当前仍使用 dir={old_key[0]} role={old_key[1]}"
            )
            _configure_handlers(allow_file_rebuild=False)
        else:
            _configure_handlers(allow_file_rebuild=True)


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

    line = _format_line(stage, camera_id=camera_id, frame_idx=frame_idx, **fields)
    _get_logger(_LOGGER_PIPELINE).info(line)


def log_pipeline_info(message: str) -> None:
    """非采样信息（启动、配置等）。"""
    if not pipeline_log_enabled():
        return
    _get_logger(_LOGGER_PIPELINE).info(f"[PIPELINE] {message}")
