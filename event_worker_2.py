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
from pick_state.experts.action_gate import (
    format_action_gate_probe_line,
    probe_action_gate_from_pipeline,
)


def _log_action_gate_probe() -> None:
    cfg = os.environ.get(
        "PICK_STATE_CONFIG", "pick_state/configs/pipeline.v5_gated.json"
    ).strip()
    try:
        info = probe_action_gate_from_pipeline(cfg)
        line = format_action_gate_probe_line(info)
        if info.get("enabled") and info.get("backend") == "onnx" and info.get("probe_ok"):
            print(f"ℹ️ {line}")
        elif info.get("enabled") and info.get("backend") != "onnx":
            print(f"⚠️ {line}（期望 backend=onnx）")
        elif info.get("enabled") and not info.get("probe_ok"):
            print(f"❌ {line}")
        else:
            print(f"ℹ️ {line}")
    except Exception as exc:
        print(f"⚠️ action_gate 探针失败 config={cfg}: {exc}")


async def _run():
    app_config = load_app_config()

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

    _log_action_gate_probe()

    worker = PickStateRedisWorker(app_config, callback_reporter=reporter)
    await worker.start()
    from services.pose_bus import POSE_STREAM_GROUP, POSE_STREAM_KEY, pose_delivery_mode

    instance_id = (
        os.environ.get("EVENT_WORKER_INSTANCE_ID", "").strip()
        or os.environ.get("HOSTNAME", "")
    )
    delivery = pose_delivery_mode()
    cfg = os.environ.get("PICK_STATE_CONFIG", "pick_state/configs/pipeline.v5_gated.json")
    if delivery == "stream":
        print(
            f"ℹ️ Event worker-2 已启动 delivery=stream pick_state={cfg} "
            f"key={POSE_STREAM_KEY} group={POSE_STREAM_GROUP} "
            f"consumer={worker._consumer_name} id={instance_id or 'local'} "
            f"callbacks={'on' if enable_cb else 'off'}"
        )
        print("⚠️ 与 visual-dps-event-worker 共用 group；对照时请只启动其中一个")
    else:
        print(
            f"ℹ️ Event worker-2 已启动 delivery=pubsub ({shard_label()}) "
            f"id={instance_id or 'local'}"
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
