# 推理 / 事件 管道拆分（S1–S5 已完成）

## 目标拓扑

```
推理容器 (每路 1 个)          事件 Worker（按 logical shard 消费）
RTSP → 检测+17点姿态  ──pose──►  碰撞/报警/回调 Java
         │ Redis                    │
         └ pose:snapshot            └ event:snapshot
                    ↘              ↙
                     UI LiveHub → SSE frame
```

## Redis 契约

| 键 / 频道 | 说明 |
|-----------|------|
| `pose:stream:{shard_id}` | Stream 分片队列（默认 16 片，`shard_id = crc32(camera_id) % 16`） |
| `pose:live:{camera_id}` | Pub/Sub，原始姿态帧 |
| `pose:snapshot:{camera_id}` | 最新姿态（TTL 10s） |
| `event:live:{camera_id}` | Pub/Sub，碰撞/报警 overlay |
| `event:snapshot:{camera_id}` | 最新事件 overlay（TTL 10s） |

**两层映射（固定 camera → shard，动态 shard → worker）**

```
camera_id ──crc32 % N──► logical shard (0..N-1) ──► pose:stream:{shard}
                              │
                              ▼（扩容时只改这层）
                    worker-A: shard 0~7
                    worker-B: shard 8~15
```

同一路 camera 永远进同一 shard，pick_state 连续状态不跨 worker。

## 水平扩展（logical shard + 多 worker）

默认 `POSE_DELIVERY=stream`：推理按 camera **XADD `pose:stream:{shard_id}`**；worker 只 **XREADGROUP** 自己负责的 shard 列表。

| 环境变量 | 默认 | 说明 |
|----------|------|------|
| `POSE_LOGICAL_SHARD_COUNT` | `16` | 全局 logical shard 数（部署后尽量不改） |
| `POSE_STREAM_KEY_PREFIX` | `pose:stream` | Stream 前缀，实际键为 `{prefix}:{shard_id}` |
| `POSE_STREAM_GROUP` | `event-workers` | 消费组（各 shard stream 共用组名） |
| `EVENT_WORKER_SHARD_START` / `END` | 空 | 本 worker 负责的 shard 闭区间 |
| `EVENT_WORKER_SHARD_IDS` | 空 | 或逗号列表，如 `0,3,7` |
| `EVENT_WORKER_CONSUMER_NAME` | 随机 | 组内消费者名，多实例须不同 |
| `POSE_DELIVERY` | `stream` | `pubsub` 为旧模式 |

未设 `START/END/IDS` 且仅 1 个 worker 时：消费 **全部** shard。

**24 路双 worker-2 示例（deploy）**

```bash
# .env
EVENT_WORKER_2_SHARD_START=0
EVENT_WORKER_2_SHARD_END=7
EVENT_WORKER_2B_SHARD_START=8
EVENT_WORKER_2B_SHARD_END=15

docker compose -f docker-compose.deploy.yml --profile worker-2 --profile worker-2-dual up -d \
  visual-dps-event-worker-2 visual-dps-event-worker-2-b
```

**勿**在同一 shard 上跑两个 consumer；**勿**与 worker-1 双开抢重叠 shard。

`POSE_LOGICAL_SHARD_COUNT=1` 时回退单流 `pose:stream`（兼容旧部署）。

UI 实时仍走 `pose:live:{cam}` Pub/Sub + snapshot，与 Stream 分片并行。

旧 pubsub 分片：`EVENT_WORKER_SHARD_COUNT` / `INDEX`（camera 直接 mod worker 数，扩容会 remap）。

---

（下文 PoseFrame / EventFrame / SSE / 部署 / RTSP 等节保持不变）

## PoseFrame / EventFrame

**PoseFrame** (`schema: 1`, `kind: "pose"`):

```json
{
  "schema": 1,
  "kind": "pose",
  "ts": 1710000000.0,
  "camera_id": "cam2",
  "frame_idx": 120,
  "infer_width": 640,
  "infer_height": 360,
  "persons": [{ "person_id": 0, "keypoints": [[x, y, score], ...] }]
}
```

**EventFrame** (`schema: 1`, `kind: "event"`):

```json
{
  "schema": 1,
  "kind": "event",
  "ts": 1710000000.0,
  "camera_id": "cam2",
  "frame_idx": 120,
  "collisions": ["shelf:box"],
  "alarm_collisions": ["shelf:box"]
}
```

## 部署

- `docker compose --profile ui up` 含 `visual-dps-event-worker`（默认 1 实例，消费全部 shard）
- 推理容器需 `REDIS_URL` + `POSE_LOGICAL_SHARD_COUNT`（由 UI 启动 infer 时注入）

## RTSP 硬件解码（NVIDIA）

| 镜像 | 说明 |
|------|------|
| `visual-dps-inference-lite` | CPU ffmpeg / OpenCV 回退 |
| `visual-dps-inference-lite-gpu` | BtbN ffmpeg + `--gpus all`，启动时探测 `cuda`/`h264_cuvid` |

## 性能说明

CPU 推理慢则姿态发布慢，事件 Worker 不会「补帧」。Stream lag 按 **shard** 观测（`scripts/monitor-pose-lag.sh`）。
