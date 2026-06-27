"""离线评测推理入口（宿主机子进程）。"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from core.config import load_app_config
from core.state import STATE
from services.benchmark_store import get_run, init_benchmark_db, update_run_status
from services.inference_backends import resolve_backend_name
from services.inference_service import InferenceService


def _apply_run_config(app_config: dict, run: dict) -> None:
    """将 run 快照中的推理参数写入 app_config（覆盖 app_config.json 默认值）。"""
    cfg = run.get("config") if isinstance(run.get("config"), dict) else {}
    mapping = {
        "inference.frame_rate": ("inference", "frame_rate", float),
        "inference.height": ("inference", "height", int),
        "inference.pose_frame_interval": ("inference", "pose_frame_interval", int),
    }
    for cfg_key, (section, key, typ) in mapping.items():
        if cfg_key not in cfg or cfg[cfg_key] is None:
            continue
        try:
            app_config.setdefault(section, {})[key] = typ(cfg[cfg_key])
        except (TypeError, ValueError):
            pass


def _apply_inference_env_overrides(app_config: dict) -> None:
    mapping = {
        "INFERENCE_FRAME_RATE": ("inference", "frame_rate", int),
        "INFERENCE_HEIGHT": ("inference", "height", int),
        "INFERENCE_POSE_FRAME_INTERVAL": ("inference", "pose_frame_interval", int),
    }
    for env_key, (section, key, typ) in mapping.items():
        raw = os.environ.get(env_key, "").strip()
        if not raw:
            continue
        try:
            app_config.setdefault(section, {})[key] = typ(raw)
        except (TypeError, ValueError):
            pass
    raw_backend = os.environ.get("INFERENCE_BACKEND", "").strip()
    if raw_backend:
        app_config.setdefault("models", {})["backend"] = raw_backend


async def _run_benchmark(run_id: str) -> int:
    init_benchmark_db()
    run = get_run(run_id)
    if not run:
        print(f"❌ 未找到 benchmark run: {run_id}", file=sys.stderr)
        return 1

    video_path = str(run.get("video_path") or "").strip()
    annotation_path = str(run.get("annotation_path") or "").strip()
    camera_id = str(run.get("camera_id") or "").strip()
    if not video_path or not os.path.isfile(video_path):
        update_run_status(run_id, "error", message=f"视频不存在: {video_path}")
        return 1
    if not annotation_path or not os.path.isfile(annotation_path):
        update_run_status(run_id, "error", message=f"标注不存在: {annotation_path}")
        return 1
    if not camera_id:
        update_run_status(run_id, "error", message="缺少 camera_id")
        return 1

    os.environ["INFERENCE_RUN_ID"] = run_id
    os.environ["INFERENCE_CAMERA_ID"] = camera_id
    os.environ.setdefault("INFERENCE_REALTIME", "0")
    os.environ.setdefault("INFERENCE_SOURCE_TYPE", "file")

    app_config = load_app_config()
    _apply_run_config(app_config, run)
    _apply_inference_env_overrides(app_config)
    backend = resolve_backend_name(app_config)
    infer_cfg = app_config.get("inference", {}) or {}
    print(
        f"ℹ️ 推理参数: pose_frame_interval={infer_cfg.get('pose_frame_interval')} "
        f"frame_rate={infer_cfg.get('frame_rate')} height={infer_cfg.get('height')}"
    )
    if run.get("backend"):
        app_config.setdefault("models", {})["backend"] = run["backend"]

    STATE.source_type = "file"
    STATE.video_path = video_path
    STATE.source_url = ""
    STATE.json_path = annotation_path
    STATE.is_inferencing = False
    STATE.upload_tag = f"bench_{run_id}"

    update_run_status(run_id, "running", message=f"推理中（{backend}）")
    print(f"ℹ️ benchmark run={run_id} camera={camera_id} backend={backend}")
    print(f"ℹ️ video={video_path}")

    service = InferenceService(app_config, STATE)
    try:
        await service.start_inference()
        task = service._background_task
        if task:
            await task
        update_run_status(run_id, "finished", message="评测完成")
        print(f"✅ benchmark run={run_id} 完成")
        return 0
    except Exception as exc:
        update_run_status(run_id, "error", message=str(exc)[:500])
        print(f"❌ benchmark run={run_id} 失败: {exc}", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="离线评测推理")
    parser.add_argument("--run-id", required=True, help="benchmark run id")
    args = parser.parse_args(argv)
    return asyncio.run(_run_benchmark(args.run_id))


if __name__ == "__main__":
    raise SystemExit(main())
