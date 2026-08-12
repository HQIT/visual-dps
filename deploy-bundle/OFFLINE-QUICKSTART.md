# Visual-DPS 离线部署（0720 分拆镜像）

## 包内容

| 路径 | 说明 |
|------|------|
| `docker-images/*.tar` | 每镜像单独 `docker save`，见 `images.manifest` |
| `app/` | compose + 配置 + 推理 bind mount 源码 |
| `weights/` | 推理权重（`install.sh` 会安装到 `app/localdata/models/`） |
| `install.sh` | load 分拆 tar + 权重 + compose up |

## 目标机（仅 Docker）

```bash
cd visual-dps-0720-deploy
./verify-package.sh
./verify-images.sh --skip-lite-cpu   # GPU 部署可跳过 CPU lite
# 见下文「现场配置清单」修改 app_config.json reporting、.env 两项与 localdata
./install.sh --host <局域网IP> --stop-infer
```

镜像 tag: **20260727-test-from-4841de6a-85288b7**

访问：`http://<MEDIAMTX_PUBLIC_HOST>:<UI_PORT>/`

---

## 现场配置清单

从源机（现网）迁到目标机时，除 Docker 镜像与权重外，需同步或改写以下内容。

### 1. 必须同步（业务数据）

| 路径 | 说明 |
|------|------|
| `app/localdata/camera_ips.json` | 摄像头列表、`source_type`（publisher / rtsp_pull / external）、`pull_url`（真实 RTSP 地址与凭据） |
| `app/localdata/json/cameras/<id>.json` | 每路标注（货架框、碰撞区域等），`<id>` 须与 `camera_ips.json` 中 `id` 一致 |
| `app/localdata/mediamtx.yml` | MediaMTX 流媒体配置；见下文生成/拷贝说明 |

`camera_ips.json` 与 `json/cameras/` 必须成对同步：少任一路的标注文件，该摄像头在 UI 无框、检测不可用。

### 2. `mediamtx.yml` 生成与手工拷贝

**优先**：`install.sh` 会在 `compose up` 前调用 `app/deploy/regenerate-mediamtx-config.sh`，按 `app/.env` + `camera_ips.json` 自动生成。

```bash
# 单独重生成（须先 docker load UI 镜像）
app/deploy/regenerate-mediamtx-config.sh app/
```

**脚本不可用时的现场拷贝**（例如 UI 镜像未 load、容器内 Python 报错）：

1. 从源机复制 `app/localdata/mediamtx.yml` 到目标机同路径；
2. 手工改 `webrtcAdditionalHosts` 为目标机 `MEDIAMTX_PUBLIC_HOST`（当前源机示例为 `192.168.1.153`）；
3. 确认 `paths` 段与 `camera_ips.json` 中各 `path`/`id` 一致；`rtsp_pull` 类型路的 `source` 为完整 RTSP URL；
4. 改完后重启 mediamtx：`docker restart visual-dps-mediamtx`。

### 3. `app/app_config.json` → `reporting`（**必核对：对齐现场 IP**）

告警/拣货完成回调走 `app/app_config.json` 的 `reporting` 段，**不会**随 `install.sh --host` 自动改写。从源机拷贝 `app_config.json` 后，必须按目标现场改回调地址，否则告警触发后 HTTP 回调会打到错误主机。

```json
"reporting": {
  "enabled": true,
  "callback_url": "",
  "callback_scheme": "http",
  "callback_ip": "<现场 WMS/上游服务 IP>",
  "callback_port": "<端口，如 8080>",
  "callback_path": "/api/yf/callback/picking-complete"
}
```

- **`callback_url` 为空时**：由 `callback_scheme` + `callback_ip` + `callback_port` + `callback_path` 拼出完整 URL；四项须与现场一致。
- **`callback_url` 已填完整 URL 时**：以该字段为准，但仍请确认其中 IP/域名属于目标现场。
- 同段内的 `task_id`、`shelf_code`、`point_code` 若与现场业务编码不同，也需一并改。
- 改完后重启 UI / event-worker 生效：`docker restart visual-dps-ui visual-dps-event-worker`。

### 4. `app/.env` 必确认项

现场只需核对以下两项（`install.sh` 可自动写入，安装前确认无误即可）：

| 变量 | 说明 |
|------|------|
| `HOST_PROJECT_ROOT` | 目标机 `app/` 目录绝对路径 |
| `MEDIAMTX_PUBLIC_HOST` | 浏览器/客户端访问的本机局域网 IP（与 `./install.sh --host <IP>` 一致） |

其余 `.env` 项（镜像 tag、推理后端、端口等）随离线包自带，一般无需改动。

### 5. 不必从源机拷贝（安装脚本处理）

| 路径 | 说明 |
|------|------|
| `app/localdata/models/` | 由包内 `weights/` 安装，或 `--weights-dir` 指定 |
| `app/localdata/logs/`、`inference/`、`frames/` | 运行时生成，可空目录 |
| `app/localdata/inference/*.status.json` | 推理状态，目标机重新启停检测即可 |

### 6. 防火墙与运行环境

目标机需放行（以 `.env` 实际值为准）：

- `UI_PORT`（如 8045 / 8046）TCP
- `8554` RTSP
- `8888` HLS
- `8889` WebRTC 信令
- `8189` WebRTC ICE（UDP + TCP）

其他要求：

- Docker + Compose v2
- 开启智能检测时 UI 容器需挂载 `/var/run/docker.sock`
- GPU 部署：`./verify-images.sh --skip-lite-cpu` 通过后再 `install.sh`

---

## 安装后验证

```bash
curl -s http://127.0.0.1:${UI_PORT:-8045}/api/version | python3 -m json.tool
docker ps | grep visual-dps
```

1. 浏览器打开 UI，确认各路视频/WebRTC 预览正常；
2. 检查标注框是否与源机一致；
3. 在总览页对需检测的路 **开启智能检测**；
4. 查看 `app/localdata/logs/pipeline/worker.log`、各 `infer_cam*.log` 无持续报错。

## 常见问题

| 现象 | 处理 |
|------|------|
| WebRTC 黑屏 / ICE 失败 | 检查 `mediamtx.yml` 的 `webrtcAdditionalHosts` 是否为当前 `MEDIAMTX_PUBLIC_HOST`；防火墙是否放行 8189 UDP+TCP |
| 某路无标注框 | 确认 `json/cameras/<id>.json` 已同步且 `id` 与 `camera_ips.json` 一致 |
| `rtsp_pull` 无画面 | 检查 `camera_ips.json` 中 `pull_url` 在目标网络是否可达 |
| 推理容器起不来 | 核对 `.env` 镜像 tag、`localdata/models/` 权重、`docker images` 与 `verify-images.sh` |
| 旧推理容器残留 | 使用 `./install.sh --stop-infer` 或手动 `docker rm -f visual-dps-infer-*` |
| 告警有触发但上游收不到回调 | 检查 `app_config.json` → `reporting` 的 `callback_ip` / `callback_port` / `callback_url` 是否已改为现场 IP；改后重启 UI 与 event-worker |

---

## GPU 部署校验镜像

```bash
./verify-images.sh --skip-lite-cpu
```

详细清单见 `app/deploy/PACKAGE-MANIFEST.md`。
