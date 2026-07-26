"""全局事件 Worker：订阅 Redis 姿态，发布碰撞/报警与 Java 回调。"""

import asyncio
import os
import signal

from core.config import load_app_config
from services.callback_reporter import CollisionCallbackReporter
from services.event_engine.sharding import shard_label
from services.event_engine.worker import EventRedisWorker
from services.pipeline_log import (
    apply_pipeline_log_config,
    configure_pipeline_logger,
    log_pipeline_info,
    pipeline_log_file_path,
)


async def _run():
    app_config = load_app_config()
    apply_pipeline_log_config(app_config)
    configure_pipeline_logger(role="worker")
    log_pipeline_info(f"Event worker 流水线日志 role=worker file={pipeline_log_file_path() or 'stdout'}")

    reporter = CollisionCallbackReporter(app_config.get("reporting", {}))
    await reporter.start()

    worker = EventRedisWorker(app_config, callback_reporter=reporter)
    await worker.start()
    from services.pose_bus import POSE_STREAM_GROUP, POSE_STREAM_KEY, pose_delivery_mode

    instance_id = os.environ.get("EVENT_WORKER_INSTANCE_ID", "").strip() or os.environ.get("HOSTNAME", "")
    delivery = pose_delivery_mode()
    if delivery == "stream":
        print(
            f"ℹ️ Event worker 已启动 delivery=stream "
            f"key={POSE_STREAM_KEY} group={POSE_STREAM_GROUP} "
            f"consumer={worker._consumer_name} id={instance_id or 'local'}"
        )
    else:
        from services.event_engine.sharding import shard_label

        print(f"ℹ️ Event worker 已启动 delivery=pubsub ({shard_label()}) id={instance_id or 'local'}")
    from services.event_engine.event_log import collision_log_enabled, prefilter_log_enabled

    if collision_log_enabled():
        print("ℹ️ 碰撞终端日志已开启 COLLISION_LOG=1（HIT / ALARM，字段与 PREFILTER 统一）")
    if prefilter_log_enabled():
        print("ℹ️ 前置门控终端日志已开启（PREFILTER_LOG 或 COLLISION_LOG=1）")

    stopping = False

    def _stop(*_args):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    while not stopping:
        await asyncio.sleep(1)

    await worker.stop()
    await reporter.stop()
    print("ℹ️ Event worker 已停止")


def main():
    asyncio.run(_run())


if __name__ == "__main__":
    main()
