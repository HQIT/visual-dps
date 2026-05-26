# 工作日报 2026-05-26

## 目标

DiDPS 视觉拣货平台：播放/抓帧/UI 精简、去掉 MediaPipe、恢复 MMPose/ONNX 双后端、新增服务总览与推理负载指标。

## 完成项

### UI / 播放（main-pr1 基线）

- 去掉 MJPEG；监控仅 HLS/WebRTC
- 浏览器 canvas 抓帧 → `POST /capture`
- 总览 ↻ 不跳转监控页
- `Dockerfile.ui` 精简，UI 容器无 GPU

### 推理与后端（main-pr2）

- **删除 MediaPipe**；`mediapipe` 配置别名映射到 `rtmpose_onnx`
- 推理后端：**MMPose GPU** / **RTMPose-T ONNX**
- 补 `services/collision_runtime.py`（修复推理容器 import 崩溃）
- GPU 推理镜像分层：`inference-lite-gpu-base` + 应用层
- 推理 worker 写入 `status.json` 的 `stats`（fps/cpu/mem/gpu）
- **服务总览** `/services` + `GET /api/services/overview`
- 总览 API 优化：避免 N 次 MediaMTX path 查询、避免重复 docker list/stats、修复已删除镜像 404

### 运维脚本

- `scripts/build-all.sh` — 异地标准构建入口
- `scripts/recreate-inference-workers.sh` — 批量用新镜像重建推理容器
- `docs/DEPLOY_REMOTE.md` — 异地部署说明

## 已知限制

- `docker/prebuild/*` 不入库，异地需自备 FFmpeg 离线包
- 服务总览 CPU/内存依赖 Docker socket；FPS/GPU 依赖推理容器运行且写入 stats
- `web/dist` 不入库，部署前必须 `npm run build`

## 分支

- `main-pr1`：UI/播放/抓帧
- `main-pr2`：推理 GPU + 服务总览 + 去 MediaPipe（本日报提交目标）
