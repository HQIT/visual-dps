# Docker Compose 交付部署

## 一键启动

工程师在宿主机准备好配置后，在项目根目录执行：

```bash
cp .env.example .env          # 修改 REDIS_PASSWORD 等
# 准备 app_config.json、localdata/camera_ips.json、localdata/json/…
# 若无 localdata/mediamtx.yml，可复制 deploy/mediamtx.yml.template

docker compose build          # 首次或代码变更后
docker compose up -d
```

访问：`http://127.0.0.1:8045`

**WSL 首次**：若 `docker pull` 超时，说明 Docker daemon 仍走旧代理。在终端执行（需 sudo 密码）：

```bash
~/bin/update-docker-proxy.sh
# 或: sudo cp deploy/docker-daemon-proxy.json /etc/docker/daemon.json && sudo systemctl restart docker
```

本机需改端口等时，可复制 `docker-compose.override.example.yml` 为 `docker-compose.override.yml`（已 gitignore，Compose 会自动合并）。

## Compose 服务

| 服务 | 容器名 | 说明 |
|------|--------|------|
| redis | visual-dps-redis | 姿态/事件总线 |
| mediamtx | visual-dps-mediamtx | RTSP/HLS/WebRTC |
| visual-dps-ui | visual-dps-ui | Web + API（管理推理容器） |
| visual-dps-event-worker | visual-dps-event-worker | 碰撞/报警/Java 回调（硬规则） |
| visual-dps-event-worker-2 | visual-dps-event-worker-2 | pick_state 备选；**profile `worker-2`**，与上者勿同时跑。见 [DEPLOY-EVENT-WORKER-2.md](./DEPLOY-EVENT-WORKER-2.md) |

推理容器 **不** 常驻 compose 内：在总览页「开启检测」时由 UI 通过 Docker API 拉起，与上述服务同属 `visual-dps-internal` 网络。

## 必挂卷

- `./app_config.json` → 全局配置
- `./localdata` → 摄像头列表、标注 JSON、mediamtx.yml、缩略图、日志库

## 视频流

- 摄像头 `source_type: publisher` 时，向 **已发布的** RTSP 推流：  
  `rtsp://127.0.0.1:8554/<path>`（path 与 camera_ips 里一致，如 cam2）
- 推理容器内自动访问 `rtsp://mediamtx:8554/<path>`

## 构建推理镜像

```bash
docker compose build visual-dps-inference-lite visual-dps-inference-lite-gpu
```

## 演示推流（可选）

MediaMTX 已在 compose 内运行后，用项目脚本向各 `path` 推流（**重启 mediamtx 后需重跑**）：

```bash
./scripts/start-mp4-rtsp-multi.sh cam1 cam2 cam3 cam4 cam5 cam6 cam7 cam8
# 单路：./scripts/start-mp4-rtsp.sh /path/to/demo.mp4 cam8
# 停止：./scripts/stop-mp4-rtsp.sh cam8
```

或手动 ffmpeg：

```bash
ffmpeg -re -stream_loop -1 -i demo.mp4 -c copy -f rtsp rtsp://127.0.0.1:8554/cam2
```

## 监控页播放（HLS / WebRTC）

- 播放地址经 UI **同源代理**：`/api/cameras/{id}/hls/…`、`/api/cameras/{id}/whep`。
- **HLS** 使用 MediaMTX `hlsVariant: mpegts`（由 `mediamtx_service` 写入 `localdata/mediamtx.yml`）。
- **WebRTC** 需宿主机映射 ICE 端口（compose 已包含）：

| 端口 | 协议 | 用途 |
|------|------|------|
| 8889 | TCP | WHEP 信令 |
| 8189 | UDP + TCP | ICE 媒体（`MEDIAMTX_WEBRTC_ICE_PORT`） |

`.env` 中 `MEDIAMTX_PUBLIC_HOST` 必须为**浏览器能访问到的 IP**（本机调试填 `127.0.0.1`）。容器内 MediaMTX 已关闭 `webrtcIPsFromInterfaces`，避免向客户端下发 172.x 地址；WHEP Answer 在 `mediamtx_proxy` 中二次改写私网 IP。

**排障**：HLS 404 / manifest 失败 → 先确认该 path 有推流（`ffprobe rtsp://127.0.0.1:8554/cam8`）。WebRTC 201 但黑屏 → 查 Windows 防火墙是否放行 UDP/TCP 8189；改代码后需 `docker compose build visual-dps-ui` 并 `restart mediamtx`。
