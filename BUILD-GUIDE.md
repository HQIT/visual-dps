# Visual-DPS 本地构建新镜像指导

> 适用：开发机本地 `docker compose` 构建与 tag 管理。  
> 推理 GPU-ONNX 锁栈细则见 [docs/BUILD-inference-gpu-onnx.md](./docs/BUILD-inference-gpu-onnx.md)。  
> 离线整包导出见 [docs/OFFLINE-DEPLOY-CHECKLIST.md](./docs/OFFLINE-DEPLOY-CHECKLIST.md)。

---

## 1. 镜像与脚本对照

| 镜像 | 构建脚本 | 说明 |
|------|----------|------|
| `visual-dps-visual-dps-ui` | `./scripts/build-ui-image.sh` | 含前端 `web/dist` + UI/API Python |
| `visual-dps-event-worker` | `./scripts/build-ui-image.sh`（同上，一次构建两个） | 事件 / 碰撞 worker |
| `visual-dps-inference-lite-gpu` | `./scripts/build-inference-lite-gpu-image.sh` | GPU 基础推理层 |
| `visual-dps-inference-lite-gpu-onnx` | `./scripts/build-inference-lite-gpu-onnx-image.sh` | GPU-ONNX 推理（常用） |
| `visual-dps-inference-lite` | `./scripts/build-inference-lite-image.sh` | CPU 推理（`INFERENCE_USE_GPU=0` 时） |

`redis:7`、`bluenviron/mediamtx:1.11.3` 为外部基础镜像，无需本地 build。

---

## 2. Tag 命名与 `.env`

### 推荐格式

```
YYYYMMDD-<分支或说明>-<git短哈希>
```

示例：`20260727-test-from-4841de6a-e539159`

### 构建前写入 `.env`（必须）

构建脚本会先 `source .env`，**命令行临时 `export VISUAL_DPS_IMAGE_TAG=...` 会被 `.env` 覆盖**，以 `.env` 为准：

```env
VISUAL_DPS_IMAGE_TAG=20260727-test-from-4841de6a-e539159
INFERENCE_LITE_IMAGE=visual-dps-inference-lite:20260727-test-from-4841de6a-e539159
INFERENCE_LITE_GPU_IMAGE=visual-dps-inference-lite-gpu:20260727-test-from-4841de6a-e539159
INFERENCE_LITE_GPU_ONNX_IMAGE=visual-dps-inference-lite-gpu-onnx:20260727-test-from-4841de6a-e539159
```

也可用辅助脚本写入（会同步上述四行）：

```bash
source scripts/lib/sync-visual-dps-image-tag-env.sh
sync_visual_dps_image_tag_env "$(pwd)" 20260727-test-from-4841de6a-e539159
```

### 构建结束后

`build-ui-image.sh` 末尾会再次调用 `sync_visual_dps_image_tag_env`，将 `.env` 中上述四行统一为本次 tag。若 UI 与推理需使用**不同 tag**，构建完成后手动改回 `INFERENCE_LITE_*` 三行。

### 自动生成 tag（可选）

未在 `.env` 指定时，格式为 `YYYYMMDD-HHMMSS-<git短哈希>`：

```bash
source scripts/lib/docker-image-tag.sh
visual_dps_image_tag
```

---

## 3. 构建前：停止运行中服务

避免旧容器占用端口或锁文件：

```bash
# 若在仓库根目录启动
docker compose down

# 若在部署目录启动（以实际 compose 路径为准）
cd /path/to/deploy/app
docker compose -f docker-compose.deploy.yml down
```

确认无残留：

```bash
docker ps | grep visual-dps
```

---

## 4. 常见场景：只构建 UI + Event Worker

**适用**：改了 `web/`、`core/`、`services/`（UI/API/worker 侧），推理逻辑通过 bind mount 热更新。

本机 `.env` 已配置 `HOST_PROJECT_ROOT=/home/hqit/workspace/visual-dps` 时，UI 启动 infer 容器会挂载宿主机源码（含 `services/pipeline_log.py` 等），**通常不必重建推理镜像**。

### 命令

```bash
cd /home/hqit/workspace/visual-dps
./scripts/build-ui-image.sh
```

脚本依次执行：`npm run build` → `docker compose build visual-dps-ui visual-dps-event-worker`。

### 构建并启动

```bash
./scripts/build-ui-image.sh --up
```

需 `.env` 中已设置 `REDIS_PASSWORD`。

### 验证

```bash
docker images | grep "${VISUAL_DPS_IMAGE_TAG}"
curl -s "http://127.0.0.1:${UI_PORT:-8045}/api/version"
```

---

## 5. 推理镜像：retag（不重建）

已从 tar 加载旧 tag 推理镜像、仅 UI/Event 重新构建时，给推理镜像打新 tag 即可（与 UI 构建顺序无关，**启动 infer 前完成**）：

```bash
TAG_OLD=20260720-test-from-4841de6a-234a98e
TAG_NEW=20260727-test-from-4841de6a-e539159

docker tag visual-dps-inference-lite-gpu:${TAG_OLD} \
           visual-dps-inference-lite-gpu:${TAG_NEW}
docker tag visual-dps-inference-lite-gpu-onnx:${TAG_OLD} \
           visual-dps-inference-lite-gpu-onnx:${TAG_NEW}
```

验证四个镜像 tag 一致：

```bash
docker images | grep "${TAG_NEW}"
# 预期：visual-dps-ui、event-worker、inference-lite-gpu、inference-lite-gpu-onnx
```

---

## 6. 从离线 tar 加载已有镜像

```bash
cd /path/to/docker-images

docker load -i bases-redis-mediamtx.tar
docker load -i visual-dps-visual-dps-ui--<TAG>.tar
docker load -i visual-dps-event-worker--<TAG>.tar
docker load -i visual-dps-inference-lite-gpu--<TAG>.tar
docker load -i visual-dps-inference-lite-gpu-onnx--<TAG>.tar
```

清单见同目录 `images.manifest`。**加载 tar 不会修改 `.env`**，tag 需自行与部署配置对齐。

---

## 7. 全量重建（含推理镜像）

**适用**：改了推理 Dockerfile / 依赖 / `services/inference_backends/`，或离线部署无 bind mount。

```bash
cd /home/hqit/workspace/visual-dps

# 1. 确认 .env 中 VISUAL_DPS_IMAGE_TAG 为目标 tag

# 2. GPU 基础层 → ONNX 层 → 校验
./scripts/build-inference-lite-gpu-image.sh
./scripts/build-inference-lite-gpu-onnx-image.sh
./scripts/verify-gpu-onnx-image.sh "visual-dps-inference-lite-gpu-onnx:${VISUAL_DPS_IMAGE_TAG}"

# 3. UI + Event
./scripts/build-ui-image.sh
```

可选同时打 `latest`：

```bash
export DOCKER_TAG_ALSO_LATEST=1
./scripts/build-ui-image.sh
```

CPU 推理（按需）：

```bash
./scripts/build-inference-lite-image.sh
```

---

## 8. 改动范围 → 是否重建对照

| 改动范围 | UI/Event 镜像 | 推理镜像 | 备注 |
|----------|---------------|----------|------|
| `web/` 前端 | 重建或 `npm run build` + `docker cp dist` | 否 | |
| `services/` worker / pipeline_log / event_engine | 重建 event-worker（或 `docker cp`） | 否* | *有 `HOST_PROJECT_ROOT` bind mount 时 infer 重启即可 |
| `services/inference_*`、Dockerfile.inference* | 否 | **重建或 retag+mount** | 无 mount 时必须重建 |
| 仅 `runtime_config.json` / 设置页开关 | 否 | 否 | 重启 infer / worker |
| Dockerfile.ui / Dockerfile.event-worker / 依赖变更 | **重建** | 视情况 | |

---

## 9. 启动服务

### 仓库根目录（开发）

```bash
cd /home/hqit/workspace/visual-dps
docker compose up -d redis mediamtx visual-dps-ui visual-dps-event-worker
```

### 部署目录

```bash
cd /path/to/deploy/app
docker compose -f docker-compose.deploy.yml up -d
```

推理容器由 UI 总览页启动，需本地已有对应 tag 的 `inference-lite-gpu-onnx` 镜像。

---

## 10. 热更新（免重建镜像）

容器已运行时，小改动可跳过完整 build：

```bash
# 前端
(cd web && npm run build)
docker cp web/dist/. visual-dps-ui:/app/web/dist/

# Python（示例）
docker cp services/pipeline_log.py visual-dps-ui:/app/services/pipeline_log.py
docker restart visual-dps-event-worker
# infer：UI 内停/启检测，或 docker restart visual-dps-infer-<cam>
```

改 Dockerfile 或 Python 依赖时必须走完整镜像构建。

---

## 11. 常见问题

| 现象 | 原因 | 处理 |
|------|------|------|
| 构建用了错误 tag | 命令行 export 被 `.env` 覆盖 | 先改 `.env` 再执行脚本 |
| 启动 infer 报镜像不存在 | `.env` tag 与本地镜像不一致 | retag 或改 `.env` 指向已有 tag |
| 首次 build 很慢（5–10 分钟） | Dockerfile 内 `apt-get` / `pip` | 正常，非脚本故障 |
| 推理日志不生效（worker 侧） | event-worker 镜像过旧 | 重建 UI/Event 或 `docker cp` 后 restart |
| 推理日志不生效（infer 侧） | 无 bind mount 且镜像过旧 | 确认 `HOST_PROJECT_ROOT`，重启 infer |

---

## 12. 相关文档

- [README-docker.md](./README-docker.md) — Compose 快速入口
- [AGENTS.md](./AGENTS.md) — Agent / CI 构建约定
- [docs/BUILD-inference-gpu-onnx.md](./docs/BUILD-inference-gpu-onnx.md) — GPU-ONNX 锁栈与探测
- [docs/DEPLOY.md](./docs/DEPLOY.md) — 部署与排障
