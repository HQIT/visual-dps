# 异地服务器构建与部署

面向已具备 **Docker + NVIDIA 驱动 + docker compose** 的 Linux 宿主机（与开发机网络环境类似，可访问镜像源）。

## 1. 获取代码与环境

```bash
git clone <仓库地址> visual-dps && cd visual-dps
git checkout main-pr2   # 或当前发布分支

cp .env.example .env    # 若无示例则向同事索取模板
# 必填：REDIS_PASSWORD、UI_PORT、MEDIAMTX_PUBLIC_HOST（浏览器访问机器的 LAN IP）
# GPU 推理：INFERENCE_USE_GPU=1
```

`localdata/` 下摄像头、标注、模型权重需按现场准备（或从现网拷贝），**不要**把开发机 `localdata/camera_ips.json` 误提交进仓库。

## 2. 一次性准备（GPU 推理）

### 2.1 离线 FFmpeg 包（仅首次）

将以下文件放到 `docker/prebuild/`（已在 `.gitignore`，需手动拷贝或从内网盘下载）：

- `jellyfin-ffmpeg7_7.1.3-6-jammy_amd64.deb`
- `ffmpeg-master-latest-linux64-gpl.tar.xz`（软解备用）

### 2.2 推理基座镜像（耗时长，极少重建）

```bash
./scripts/build-inference-lite-gpu-base.sh
# 产物: visual-dps-inference-lite-gpu-base:latest
```

### 2.3 推理应用层 + UI + Event Worker

```bash
./scripts/build-all.sh
```

等价于：

```bash
./scripts/build-inference-lite-gpu-image.sh   # 秒级，依赖 base
cd web && npm ci && npm run build && cd ..
docker compose build visual-dps-ui visual-dps-event-worker
```

## 3. 启动

```bash
export REDIS_PASSWORD='你的密码'
docker compose up -d
```

浏览器访问：`http://<宿主机IP>:${UI_PORT:-8046}/`

- 首页：摄像头总览  
- **服务总览**：`/services`（基础设施 + 推理 FPS/CPU/内存/GPU）  
- 事件矩阵：`/matrix`

## 4. 推流与推理

```bash
# RTSP 测试流（MediaMTX 重启后若断流需重跑）
./scripts/start-mp4-rtsp-multi.sh

# UI 内对每路点击「开启检测」；或批量重建推理容器：
./scripts/recreate-inference-workers.sh
```

推理镜像默认：`visual-dps-inference-lite-gpu:latest`（`INFERENCE_USE_GPU=1` 时）。

## 5. 常见问题

| 现象 | 处理 |
|------|------|
| 服务总览「镜像」列 404 | 已修复：勿访问已删除镜像元数据；拉最新代码 |
| 首页/总览一直加载 | 勿并发多页轮询；总览已优化为轻量 API |
| WebRTC 黑屏 | `MEDIAMTX_PUBLIC_HOST` 设为浏览器能访问的 IP |
| 推理容器 Exited | `docker logs visual-dps-infer-cam1`；缺 `collision_runtime` 等需重建应用层镜像 |

## 6. 仅更新 UI（不改推理）

```bash
cd web && npm run build && cd ..
docker compose build visual-dps-ui
docker compose up -d visual-dps-ui
```

## 7. 仅更新推理逻辑

```bash
./scripts/build-inference-lite-gpu-image.sh
./scripts/recreate-inference-workers.sh
```
