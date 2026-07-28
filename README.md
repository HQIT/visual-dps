# visual-dps

视觉拣货监护服务：货架区域标注、人体姿态推理、手腕/货框碰撞检测、拣货完成回调。

合并自 `box_human_det`（推理）与 `annotation_tool`（标注），统一为单个 FastAPI 服务。

**接手开发请先阅读 [HANDOFF.md](./HANDOFF.md)**（合并思路、原则与后续入口）。

**现场使用请阅读 [docs/USER_MANUAL.md](./docs/USER_MANUAL.md)**（安装、FFmpeg 推流、标注、开启检测、监控与排障，含截图）。

## 页面

| 路径 | 说明 |
|------|------|
| `/` | 推理监控（上传视频、启动推理、可视化） |
| `/annotate` | 标注配置（多货架、摄像头 IP、抓帧标注） |

## 配置说明（`app_config.json`）

### video — 视频采集/转码

| 字段 | 含义 |
|------|------|
| `transcode_height` | 上传视频转码目标高度（像素，等比缩放） |
| `capture_height` | 抓帧/首帧用于标注的目标高度（像素） |

### inference — 推理管线

| 字段 | 含义 |
|------|------|
| `frame_rate` | 推理处理帧率（fps），限制每秒处理帧数 |
| `height` | 推理输入高度（像素，等比缩放，不放大） |
| `pose_frame_interval` | 姿态估计帧间隔（每 N 帧运行一次关键点模型） |
| `stream_buffer_size` | 网络流解码缓冲帧数（1=仅保留最新帧，降低 RTSP 延迟） |
| `preview_max_width` | WebSocket 预览推送最大宽度（像素） |
| `preview_jpeg_quality` | 预览 JPEG 质量（1–100） |

推理行为：

- 每个 processed 帧都执行人体检测
- 姿态估计按 `pose_frame_interval` 跳帧，中间帧复用上次骨架结果
- 可视化关闭时不编码 JPEG，仅后台推理与回调

### pipeline_log — 推理流水线阶段日志

用于观测 **RTSP 采帧 → 推理发布 pose → Event Worker 消费 → 事件发布 / 回调入队** 各阶段行为。推理链路（infer 容器、event-worker、RTSP/后端/回调）**统一经 Python `logging` 输出**，由 `services/pipeline_log.py` 配置各 logger 的 Handler；`[PIPELINE]` 行前缀表示流水线阶段 trace。

**输出机制（均为 logging，非 print）**：

| Handler | 配置项 | 去向 |
|---------|--------|------|
| `StreamHandler` → stdout | `stdout` | `docker logs` 可见 |
| `RotatingFileHandler` | `file_enabled` + `dir` | `{dir}/{role}.log`（如 `worker.log`、`infer_cam1.log`） |

二者可同时挂载：`stdout=true` 且 `file_enabled=true` 时，同一条 `[PIPELINE]` 既进容器日志又落盘。仅当 `enabled=true` 时才会产生 `[PIPELINE]` 行。

**配置优先级**（高 → 低）：`localdata/runtime_config.json` → `app_config.json` → 环境变量 `PIPELINE_LOG*`。  
**设置页**：全局配置 → 流水线日志（与下表字段一一对应）。

| 字段 | 含义 |
|------|------|
| `enabled` | 是否记录 `[PIPELINE]` 阶段日志（默认 `false`；关则无任何 pipeline 阶段行） |
| `stdout` | 是否为 `[PIPELINE]` 挂载 stdout Handler（默认 `true`；`docker logs` 可见；需 `enabled=true`） |
| `file_enabled` | 是否为 `[PIPELINE]` 挂载文件 Handler（RotatingFileHandler；可与 stdout 同时开启） |
| `dir` | 文件 Handler 的目录，默认 `localdata/logs/pipeline` |
| `sample` | 帧级 stage 采样间隔：每 N 帧输出一条（默认 `30`）；`callback_enqueued` 不受采样限制 |
| `max_bytes` | 单文件大小上限（字节），默认 `52428800`（50MB），超出后轮转 |
| `backup_count` | 轮转保留的历史文件数，默认 `5`；`0` 表示仅覆盖当前文件 |

环境变量可临时覆盖（便于排障）：`PIPELINE_LOG`、`PIPELINE_LOG_FILE`、`PIPELINE_LOG_DIR`、`PIPELINE_LOG_SAMPLE`、`PIPELINE_LOG_STDOUT`、`PIPELINE_LOG_MAX_BYTES`、`PIPELINE_LOG_BACKUP_COUNT`。

**日志文件路径**（`enabled=true` 且 `file_enabled=true` 时）：

| 进程 | 文件 |
|------|------|
| Event Worker | `{dir}/worker.log` |
| 推理容器 camX | `{dir}/infer_camX.log` |

**热生效**：设置页保存后，event-worker 与 infer 容器对 **enabled / 采样 / stdout** 可热更新（重建 logging Handler）；变更 **dir / max_bytes / backup_count** 需重启 event-worker 与各 `visual-dps-infer-*`。

**示例**（`app_config.json`）：

```json
"pipeline_log": {
    "enabled": false,
    "file_enabled": false,
    "dir": "localdata/logs/pipeline",
    "sample": 30,
    "stdout": true
}
```

`max_bytes`、`backup_count` 可在设置页或 `runtime_config.json` 中配置。

#### `[PIPELINE]` 日志行字段

每行格式：`[PIPELINE] key=value ...`（纯文本，便于 `grep`）。

**公共字段**（每条必有）：

| 字段 | 含义 |
|------|------|
| `time` | 墙钟时间（`services/wall_clock.py` 统一 TZ，默认 `Asia/Shanghai`；无 tzdata 时 UTC+8） |
| `stage` | 流水线阶段名（见下表） |
| `camera` | 摄像头 ID |
| `frame` | 帧序号 |

**阶段（stage）与附加字段**：

| stage | 发生位置 | 附加字段 |
|-------|----------|----------|
| `rtsp_frame` | 推理容器采帧 | `run_id`、`captured_at`（采帧时刻，秒） |
| `infer_pose_done` | 推理 pose 完成 | `run_id`、`persons`、`det_ms`、`pose_ms` |
| `pose_published` | pose 写入 Redis | `run_id`、`persons`、`delivery`（如 `stream`） |
| `worker_received` | Event Worker 收到 pose | `run_id`、`persons` |
| `worker_done` | 碰撞/门控处理完成 | `run_id`、`worker_ms`、`hits`、`alarms` |
| `event_published` | 事件快照发布 | `run_id`、`published`、`hits`、`alarms` |
| `callback_enqueued` | 告警回调入队 | `run_id`、`box_id`、`collision`（不采样） |

跨进程对齐：`camera` + `frame` + `run_id`（推理会话启动时生成的 12 位 hex，随 pose 传递）。

**相关但非 `[PIPELINE]` 的日志**（同属 `pipeline_log.py` 的 logging logger，Handler/开关独立；**同样不是 print**）：

| 前缀 / Logger | 环境变量或条件 | 输出 | 内容 |
|---------------|----------------|------|------|
| 启动提示 | 始终 | 固定 stdout | `visual_dps.boot`：进程启停、delivery 模式 |
| 推理运行时 | 始终 | 固定 stdout | `visual_dps.inference`：模型加载、RTSP 回退、推理参数 |
| `[COLLISION]` | `COLLISION_LOG=1` | stdout（+ 可选文件） | 碰撞 HIT / 告警 ALARM |
| `[PREFILTER]` | `PREFILTER_LOG=1` 或 `COLLISION_LOG=1` | stdout（+ 可选文件） | 前置门控 PASS / FILTERED |
| `[CALLBACK]` | `reporting.enabled` | stdout（+ 可选文件） | Java 回调 SEND / ACK / FAILED |

开启 `file_enabled` 时，collision/prefilter/callback 等与 `[PIPELINE]` 共用同一 `{role}.log`，按行前缀区分。

### source — 启动时视频源

```json
"source": {
  "enabled": true,
  "stream_url": "rtsp://...",
  "upload_tag": "stream_config",
  "annotation_json": "localdata/json/annotation_xxx.json"
}
```

## 启动

```bash
pip install fastapi "uvicorn[standard]" python-multipart opencv-python-headless numpy psutil redis

python main.py
```

Docker：

```bash
docker compose up -d --build
```

## 主要 API

- `POST /api/upload_video` — 上传视频
- `GET /api/get_first_frame` — 获取首帧
- `GET/POST/DELETE /api/camera_ips` — 摄像头地址管理
- `POST /api/get_camera_frame` — 摄像头抓帧
- `POST /api/save_annotation` — 保存标注 JSON
- `GET /api/annotation` — 读取标注 JSON
- `POST /api/start_inference` — 启动推理
- `WS /ws/inference` — 实时推理推流

## 标注 JSON 格式

支持两种格式：

1. 旧格式：顶层 `boxes[]`
2. 新格式（多货架）：`shelves[].boxes[]`，含 `annotation_size`、`source_info`、`video_polygon_norm`

## 轻量推理（本地测试平替）

默认使用 **RTMPose-T ONNX**（`models.backend: rtmpose_onnx`）；GPU 部署用 `./scripts/build-inference-lite-gpu-onnx-image.sh`（镜像 tag 带日期，见 `AGENTS.md`）。

构建轻量推理镜像后，在 `.env` 中设置脚本输出的 `INFERENCE_LITE_GPU_ONNX_IMAGE`（带日期 tag）。

```bash
./scripts/build-inference-lite-gpu-onnx-image.sh
docker compose up -d visual-dps-ui
```

`app_config.json` 中 `"models": { "backend": "rtmpose_onnx" }`。  
碰撞/告警逻辑与默认后端共用（COCO-17 肩/腕关键点），精度低于 RTMPose，仅建议开发验证。

## 后续计划

统一配置 UI（热生效、单页集中、最少配置项）见 [ROADMAP.md](./ROADMAP.md)。
