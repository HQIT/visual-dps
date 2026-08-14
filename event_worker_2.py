"""事件 Worker-2：pick_state 算法；与 worker-1 共用 pose:stream / event-workers。

对照时只启停其中一个服务，勿双开抢同一 group。
"""

import asyncio
import os
import signal

from core.config import load_app_config
from services.callback_reporter import CollisionCallbackReporter
from services.event_engine.pick_state_worker import PickStateRedisWorker
from services.event_engine.sharding import shard_label
from pick_state.pipeline.timing import stage_profiling_enabled
from services.pipeline_log import (
    configure_process_logging,
    get_boot_logger,
    log_pipeline_info,
    pipeline_log_file_path,
)


async def _run():
    app_config = load_app_config()
    # docker cp 热更新场景：无 compose 重建时也能开分阶段 profiling + 固定 consumer
    os.environ.setdefault("EVENT_WORKER_CONSUMER_NAME", "worker-2-main")
    if stage_profiling_enabled():
        os.environ.setdefault("PIPELINE_LOG", "1")
        os.environ.setdefault("PIPELINE_LOG_FILE", "1")
        os.environ.setdefault("PIPELINE_LOG_DIR", "/app/localdata/logs/pipeline")
        os.environ.setdefault("PIPELINE_LOG_SAMPLE", "10")

    configure_process_logging(role="event_worker_2", app_config=app_config)
    log_pipeline_info(
        f"Event worker-2 流水线日志 role=event_worker_2 file={pipeline_log_file_path() or 'stdout'}"
    )

    enable_cb = os.environ.get("EVENT_WORKER_ENABLE_CALLBACKS", "1").strip() not in (
        "0",
        "false",
        "False",
        "no",
    )
    reporter = None
    if enable_cb:
        reporter = CollisionCallbackReporter(app_config.get("reporting", {}))
        await reporter.start()

    worker = PickStateRedisWorker(app_config, callback_reporter=reporter)
    await worker.start()
    from services.pose_bus import POSE_STREAM_GROUP, POSE_STREAM_KEY, pose_delivery_mode

    instance_id = (
        os.environ.get("EVENT_WORKER_INSTANCE_ID", "").strip()
        or os.environ.get("HOSTNAME", "")
    )
    delivery = pose_delivery_mode()
    cfg = os.environ.get("PICK_STATE_CONFIG", "pick_state/configs/pipeline.v5_gated.json")
    boot = get_boot_logger()
    stage_prof = "on" if stage_profiling_enabled() else "off"
    if delivery == "stream":
        boot.info(
            f"ℹ️ Event worker-2 已启动 delivery=stream pick_state={cfg} "
            f"key={POSE_STREAM_KEY} group={POSE_STREAM_GROUP} "
            f"consumer={worker._consumer_name} id={instance_id or 'local'} "
            f"callbacks={'on' if enable_cb else 'off'} "
            f"stage_profile={stage_prof or 'off'}"
        )
        print("⚠️ 与 visual-dps-event-worker 共用 group；对照时请只启动其中一个")
    else:
        boot.info(
            f"ℹ️ Event worker-2 已启动 delivery=pubsub ({shard_label()}) "
            f"id={instance_id or 'local'} stage_profile={stage_prof or 'off'}"
        )

    stopping = False

    def _stop(*_args):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    while not stopping:
        await asyncio.sleep(1)

    await worker.stop()
    if reporter is not None:
        await reporter.stop()
    print("ℹ️ Event worker-2 已停止")


def main():
    asyncio.run(_run())


if __name__ == "__main__":
    main()
