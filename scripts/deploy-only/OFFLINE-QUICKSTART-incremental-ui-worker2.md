# Visual-DPS 离线部署（0820 增量包 · UI + worker + worker-2）

> **文档来源**：在 [`visual-dps-0817-deploy/OFFLINE-QUICKSTART.md`](../../visual-dps-0817-deploy/OFFLINE-QUICKSTART.md) 基础上改写。  
> **0817 文档校验（2026-08-20）**：下列章节**仍可沿用**——「现场配置清单」「防火墙与运行环境」「安装后验证」思路、「常见问题」大部分条目；下列内容**已更新/不适用**，请勿照搬 0817 旧 tag：
>
> | 0817 原文 | 0820 差异 |
> |-----------|-----------|
> | 镜像 tag `20260817-feature-eventworker2-0b26d8a` | 本包 tag **`__TAG__`** |
> | 含 UI + worker-1 + worker-2 三个 tar | 本包同样 **三个 tar**，worker-1 为 **retag 对齐 tag**（内容与 0817 相同） |
> | retag 需显式选 0813 / 0727 旧 tag | **可省略旧 tag**，自动尝试 **0817 → 0813 → 0727** |
> | worker-2 初版 ONNX 门控 | worker-2 **共享 ONNX Session + 启动探针**（07082d2） |
> | 包目录 `visual-dps-0817-deploy` | **`__PACKAGE_NAME__`** |
> | git `0b26d8a` | git **`__GIT_COMMIT__`**（`__GIT_BRANCH__`） |

---

## 包内容

| 路径 | 说明 |
|------|------|
| `docker-images/*.tar` | **UI、event-worker、event-worker-2**（见 `images.manifest`） |
| `app/` | compose + 配置 + 推理 bind mount 源码（含 ORT 线程 env、`docker-compose.deploy.yml` 中 worker-2 profile） |
| `weights/` | 推理权重（`install.sh` 会安装到 `app/localdata/models/`；现场已有权重可跳过重复安装） |
| `install.sh` | load 分拆 tar + 权重 + compose up（支持 `--worker-2`） |
| `scripts/load-split-images.sh` | 按 manifest 加载三个业务镜像 tar |
| `scripts/retag-infer-images.sh` | 现场将**旧 tag 推理镜像** retag 为新 tag；省略旧 tag 时自动探测；GPU 用 `--skip-lite-cpu` |
| `scripts/lib/docker-cmd.sh` | 自动检测 docker 权限，无权限时使用 `sudo docker` |
| `DEPLOY-EVENT-WORKER-2.md` | worker-2 切换/回滚细则 |
| `PACKAGE_INFO.txt` / `BUILD_TAG.txt` | 包元数据（含 `INFER_RETAG_OLD*` 候选旧 tag） |

**本包不含**：`redis:7`、`bluenviron/mediamtx:1.11.3`、inference 镜像 tar——**目标机应已存在**（如曾部署 0727 / 0813 / 0817 包或同栈）。

镜像 tag: **`__TAG__`**

---

## 适用场景

- 目标机 **已有** Visual-DPS 基础栈（redis / mediamtx / infer 镜像）。
- 需要升级 **UI + event-worker（worker-1 备选）+ event-worker-2**。
- **推理镜像本次不升级**（无 infer tar）；通过 **retag** 对齐新 tag 即可。
- 0820 worker-2 相对 0817：**action_gate 共享 ONNX Session**、**启动探针**、ORT/BLAS 线程收敛（见 UI `.env` 与 bind mount）。

### 相对 0817 包的本版变更摘要

| 项 | 0817 | 0820 |
|----|------|------|
| 镜像 tar | UI + worker-1 + worker-2 | **UI + worker-1 + worker-2**（与 0817 同 layout） |
| worker-2 | action_gate ONNX | ONNX + **共享 Session / 启动探针**（07082d2） |
| worker-1 | 含 tar，可对齐 tag | **含 tar**（0817 层 retag 至 07082d2，功能未变） |
| infer | 仅 retag | **仍仅 retag**，锁栈未变 |
| UI | retag 对齐 | retag 对齐（07082d2）；含 ORT 线程相关 env |
| retag 脚本 | 显式传旧 tag | **可省略旧 tag**，自动 0817 → 0813 → 0727 |

---

## 现场 sudo 说明

153 等目标机 **docker 命令通常需 sudo**。本包 `install.sh` / `load-split-images.sh` / `retag-infer-images.sh` / `verify-images.sh` **已自动检测**：无 docker 组权限时使用 `sudo docker`。

手动执行时请加 sudo，例如：

```bash
sudo docker ps
sudo docker images | grep visual-dps
```

强制全程 sudo：`export VISUAL_DPS_DOCKER_SUDO=1` 后再运行安装脚本。

---

## 目标机安装（增量 · 推荐 worker-2）

```bash
cd __PACKAGE_NAME__
./verify-package.sh

# 1) 加载本包三个镜像 tar（UI + worker + worker-2）
./scripts/load-split-images.sh

# 2) 推理镜像 retag（推荐省略旧 TAG，自动 0817 → 0813 → 0727）
./scripts/retag-infer-images.sh --skip-lite-cpu
# 或显式指定旧 tag：
# ./scripts/retag-infer-images.sh --skip-lite-cpu 20260817-feature-eventworker2-0b26d8a
# ./scripts/retag-infer-images.sh --skip-lite-cpu 20260813-feature-eventworker2-5e4f4fe
# ./scripts/retag-infer-images.sh --skip-lite-cpu 20260727-test-from-4841de6a-85288b7

# 3) 校验镜像（GPU 现场跳过 CPU lite）
./verify-images.sh --skip-lite-cpu

# 4) 见下文「现场配置清单」核对 app/.env、app_config.json、localdata

# 5) 安装并启动（pick_state / worker-2 · ONNX）
./install.sh --host <局域网IP> --worker-2 --stop-infer
```

若仍用硬规则 worker-1（**与 worker-2 勿双开**）：

```bash
./install.sh --host <局域网IP> --stop-infer
# 不要加 --worker-2；且确保 visual-dps-event-worker-2 未 Running
# worker-1 镜像已随本包 load，tag 为 __TAG__
```

访问：`http://<MEDIAMTX_PUBLIC_HOST>:<UI_PORT>/`（本包 `.env` 示例 `UI_PORT=8046`）

---

## 推理镜像 retag（`scripts/retag-infer-images.sh`）

本包**不含** inference 镜像 tar；`.env` 里 `INFERENCE_LITE_*` 已改为新 tag **`__TAG__`**，但目标机 Docker 里推理镜像往往仍是旧 tag。

`retag-infer-images.sh` 用 `docker tag` 把旧 tag **复制**为新 tag，**不重建、不下载**镜像，使 `verify-images` 与 UI 启停推理能按新 tag 找到 gpu / gpu-onnx。

### 何时需要

| 情况 | 是否 retag |
|------|------------|
| 目标机从未部署过 Visual-DPS，且无 gpu/gpu-onnx 镜像 | **不适用本包**；需全量离线包（如 0727）或先装含 infer 的旧包 |
| 曾部署 **0727**，infer 仍是 `20260727-...` | **需要** |
| 曾部署 **0813 增量**，infer 为 `20260813-...` | **需要** |
| 曾部署 **0817 增量**，infer 为 `20260817-...0b26d8a` | **需要**（0820 最常见） |
| 本地已有新 tag 的 gpu / gpu-onnx | 可跳过；脚本会对已有新 tag 输出 `SKIP (已有)` |

### 旧 tag 选择（0820 现场）

| 现场现状 | 推荐 retag 命令 |
|----------|-----------------|
| 刚装过 **0817** 增量包 | `./scripts/retag-infer-images.sh --skip-lite-cpu`（自动探测） |
| 曾装 **0813**、未装 0817 | 同上，或显式 `... 20260813-feature-eventworker2-5e4f4fe` |
| 只有 **0727** 全量栈 | 同上，或显式 `... 20260727-test-from-4841de6a-85288b7` |
| 不确定 | `docker images \| grep inference-lite` 看现有 tag |

省略旧 TAG 时脚本按 `BUILD_TAG.txt` + 内置顺序尝试：**0817 → 0813 → 0727**。

### 用法

```bash
./scripts/retag-infer-images.sh --help
```

| 参数 / 选项 | 说明 |
|-------------|------|
| `[旧TAG]` | 可选；指定则只从该 tag retag；**省略则自动依次尝试** 0817 / 0813 / 0727 |
| `[新TAG]` | 省略时从 `app/.env` 的 `VISUAL_DPS_IMAGE_TAG` 读取 |
| `--skip-lite-cpu` | **GPU 现场推荐**：不处理 `visual-dps-inference-lite`（CPU 镜像，现场常不存在） |

### 处理的镜像

| 镜像 | GPU 现场 | 说明 |
|------|----------|------|
| `visual-dps-inference-lite` | **非必需** | 加 `--skip-lite-cpu` 跳过；未加则缺镜像仅 WARN |
| `visual-dps-inference-lite-gpu` | **必需** | 缺旧 tag 且无新 tag → 脚本 exit 1 |
| `visual-dps-inference-lite-gpu-onnx` | **必需** | 同上 |

### 推荐命令（0820 · GPU · 自 0817 升级）

```bash
cd __PACKAGE_NAME__

./scripts/retag-infer-images.sh --skip-lite-cpu
./verify-images.sh --skip-lite-cpu
```

### 推荐命令（0820 · GPU · 自 0813 升级）

```bash
cd __PACKAGE_NAME__

./scripts/retag-infer-images.sh --skip-lite-cpu 20260813-feature-eventworker2-5e4f4fe
./verify-images.sh --skip-lite-cpu
```

### 推荐命令（0820 · GPU · 自 0727 升级）

```bash
cd __PACKAGE_NAME__

./scripts/retag-infer-images.sh --skip-lite-cpu 20260727-test-from-4841de6a-85288b7
./verify-images.sh --skip-lite-cpu
```

显式指定新旧 tag（0817 → 0820）：

```bash
./scripts/retag-infer-images.sh --skip-lite-cpu \
  20260817-feature-eventworker2-0b26d8a \
  __TAG__
```

### 与 `verify-images.sh` 的关系

- **先 retag，再 verify**；顺序反了 verify 会报缺 infer 镜像。
- retag 的 `--skip-lite-cpu` 与 verify 的 `--skip-lite-cpu` **应成对使用**（GPU 部署）。
- retag **只改 Docker 本地 tag**，不改 `app/.env`；`.env` 已由本包写好新 tag。

### 自检

```bash
docker images | grep -E 'inference-lite|__TAG__'
# 期望可见 ...-gpu 与 ...-gpu-onnx 的新 tag；可无 visual-dps-inference-lite
```

---

## event-worker-2（pick_state · ONNX · 0820）

| 项 | 说明 |
|----|------|
| 容器名 | `visual-dps-event-worker-2` |
| 与 worker-1 | **备选**，同一时刻**只启一个** |
| Redis | 共用 `pose:stream`、消费组 `event-workers` |
| 算法 | 镜像内 `pick_state/configs/pipeline.v5_gated.json` |
| 动作门控 | **`backend=onnx`**，`models/action_gate_v1/model.onnx`，依赖 **onnxruntime** |
| 0820 增强 | **共享 ONNX Session**（降低多路 CPU）、**启动探针**（启动时校验 gate 可加载） |
| 回退 sklearn | 改配置 `backend=sklearn` 并确保 `model.joblib` 存在（一般不必） |
| compose | `docker compose --profile worker-2 up -d visual-dps-event-worker-2` |

### 从已在跑的 worker-1 切换到 worker-2

在 **`app/`** 目录（或包根目录用 `-f app/docker-compose.deploy.yml`）：

```bash
cd app
set -a && source .env && set +a

docker compose -f docker-compose.deploy.yml stop visual-dps-event-worker
docker compose -f docker-compose.deploy.yml --profile worker-2 up -d visual-dps-event-worker-2

docker ps --filter name=visual-dps-event-worker --format '{{.Names}} {{.Status}}'
# 期望：仅 visual-dps-event-worker-2 为 Up
```

### 回滚到 worker-1（硬规则）

```bash
cd app
docker compose -f docker-compose.deploy.yml --profile worker-2 stop visual-dps-event-worker-2
docker compose -f docker-compose.deploy.yml up -d visual-dps-event-worker
# worker-1 镜像 tag 须为 __TAG__（本包已 load）
```

### worker-2 启动与 ONNX 校验

```bash
docker logs --tail=80 visual-dps-event-worker-2
# 期望：Event worker-2 已启动 delivery=stream pick_state=.../pipeline.v5_gated.json ...
# 0820：启动探针通过 action_gate ONNX 加载

# 确认 onnxruntime 与 ONNX 门控可加载
docker exec visual-dps-event-worker-2 python -c "
import onnxruntime as ort
from pick_state.experts.action_gate import ActionGate
g = ActionGate({'enabled': True, 'backend': 'onnx', 'model_path': 'models/action_gate_v1/model.joblib'})
print('onnxruntime', ort.__version__, 'backend', g.backend)
"

# UI 开 1～2 路智能检测，观察告警与回调
docker exec visual-dps-redis redis-cli -a "\$REDIS_PASSWORD" --no-auth-warning XINFO GROUPS pose:stream
# lag 应接近 0；若 worker-1 与 worker-2 同时 Running 会抢消息导致 lag/漏检
```

更细步骤见包内 **`DEPLOY-EVENT-WORKER-2.md`**。

---

## 本机模拟安装（验证离线包 · 可选）

用于在构建机模拟「现场已有 0817/0813/0727 栈 → 装 0820 增量包」：

```bash
# 前提：本地仍有旧 tag 的 infer 镜像，且已 docker rmi 新 tag 的 UI / worker-2 镜像

cd /home/hqit/workspace/__PACKAGE_NAME__
./verify-package.sh
./scripts/load-split-images.sh
./scripts/retag-infer-images.sh --skip-lite-cpu
./verify-images.sh --skip-lite-cpu

# 修改 app/.env：HOST_PROJECT_ROOT、MEDIAMTX_PUBLIC_HOST
./install.sh --host 127.0.0.1 --worker-2 --stop-infer
```

---

## 现场配置清单

（与 0817 / 0813 / 0727 文档一致；自源机迁数据时仍需同步。）

### 1. 必须同步（业务数据）

| 路径 | 说明 |
|------|------|
| `app/localdata/camera_ips.json` | 摄像头列表、`source_type`（publisher / rtsp_pull / external）、`pull_url` |
| `app/localdata/json/cameras/<id>.json` | 每路标注；`<id>` 须与 `camera_ips.json` 一致 |
| `app/localdata/mediamtx.yml` | MediaMTX 配置；见下文 |

`camera_ips.json` 与 `json/cameras/` 必须成对：缺标注则 UI 无框、检测不可用。

### 2. `mediamtx.yml` 生成与手工拷贝

**优先**：`install.sh` 在 compose up 前调用 `app/deploy/regenerate-mediamtx-config.sh`。

```bash
app/deploy/regenerate-mediamtx-config.sh app/
```

**脚本不可用时**：从源机复制 `app/localdata/mediamtx.yml`，改 `webrtcAdditionalHosts` 为目标 `MEDIAMTX_PUBLIC_HOST`，核对 `paths` 与 `camera_ips.json` 一致后 `docker restart visual-dps-mediamtx`。

### 3. `app/app_config.json` → `reporting`

回调地址**不会**随 `install.sh --host` 自动改；须按现场 WMS/上游 IP 核对 `reporting.callback_ip` / `callback_port` / `callback_url`。

改完后重启 UI 与**当前在用的 event-worker**：

```bash
# worker-2 场景
docker restart visual-dps-ui visual-dps-event-worker-2

# worker-1 场景
docker restart visual-dps-ui visual-dps-event-worker
```

### 4. `app/.env` 必确认项

| 变量 | 说明 |
|------|------|
| `HOST_PROJECT_ROOT` | 目标机 **`app/` 绝对路径**（示例：`.../__PACKAGE_NAME__/app`） |
| `MEDIAMTX_PUBLIC_HOST` | 浏览器访问用局域网 IP（与 `./install.sh --host` 一致） |
| `VISUAL_DPS_IMAGE_TAG` | 须为 **`__TAG__`**（与 load 的 UI/worker-2 镜像一致） |
| `REDIS_PASSWORD` | 不可用占位符 `change-me` |
| `INFERENCE_LITE_GPU_ONNX_IMAGE` 等 | 已随包写入新 tag；**镜像本体**靠 retag 对齐 |
| `ORT_*` / `OMP_NUM_THREADS` 等 | 0820 包已写入线程收敛默认值；多路压测后可按现场微调 |

GPU 现场**不要求**本地存在 `visual-dps-inference-lite`（CPU）；retag 与 verify 均用 `--skip-lite-cpu`。

### 5. 不必从源机拷贝

| 路径 | 说明 |
|------|------|
| `app/localdata/models/` | 包内 `weights/` 安装，或现场已有则不必覆盖 |
| `app/localdata/logs/`、`inference/`、`frames/` | 运行时生成 |

### 6. 防火墙与运行环境

放行（以 `.env` 为准）：`UI_PORT` TCP、`8554` RTSP、`8888` HLS、`8889` WebRTC、`8189` ICE（UDP+TCP）。

要求：Docker Compose v2；UI 容器挂载 `docker.sock`（启停推理）；GPU 场景 `./verify-images.sh --skip-lite-cpu` 通过后再 install。

---

## 安装后验证

```bash
curl -s http://127.0.0.1:${UI_PORT:-8046}/api/version | python3 -m json.tool
docker ps | grep visual-dps
```

1. 浏览器打开 UI，预览与标注正常；
2. 总览页对需检测的路 **开启智能检测**；
3. worker-2：`docker logs --tail=50 visual-dps-event-worker-2` 无持续报错；ONNX 自检见上文；
4. 多路场景：观察 Redis `pose:stream` 的 **lag**（worker-2 单实例）；0820 共享 Session 相对 0817 进一步降低 worker-2 CPU；16 路为 sweet spot，22+ 路需评估 lag；
5. 日志：`app/localdata/logs/pipeline/worker.log`（worker-1）或 worker-2 容器日志；`infer_cam*.log` 见摄像头 pipeline 设置。

---

## 常见问题

| 现象 | 处理 |
|------|------|
| `verify-images` 缺 infer 镜像 | 先 `./scripts/retag-infer-images.sh --skip-lite-cpu`，再 `verify-images.sh --skip-lite-cpu` |
| 缺 `visual-dps-inference-lite` | **正常**（GPU 现场）；retag/verify 均加 `--skip-lite-cpu` |
| retag 报 FAIL 缺 gpu/gpu-onnx | 确认现场曾有 infer 镜像；显式传旧 tag 或装全量包 |
| `permission denied` 连 docker | 脚本应自动 sudo；或 `export VISUAL_DPS_DOCKER_SUDO=1` |
| worker-2 启动报 `No module named 'onnxruntime'` | 未 load 本包 **worker-2 tar**，或用了旧 tag 镜像；重新 `load-split-images.sh` |
| worker-2 启动报找不到 `model.onnx` | 确认镜像 tag 为 **`__TAG__`** 且为本次 build，勿混用 0817/0813 worker-2 |
| 告警变少 / 漏检一半 | **worker-1 与 worker-2 同时 Running** → 停掉其中一个 |
| worker-2 起不来 / Image not found | `docker images \| grep event-worker-2`；确认 load 过本包 tar |
| WebRTC 黑屏 | 检查 `mediamtx.yml` 的 `webrtcAdditionalHosts` 与防火墙 8189 |
| 推理容器起不来 | retag 后 `docker images` 含新 tag 的 gpu-onnx；权重在 `localdata/models/` |
| 回调不到上游 | 改 `app_config.json` → `reporting` 后 restart UI + 当前 worker |
| 多路 lag 顶满 ~2000 | 单 worker-2 过载；减路数或规划双 worker-2 分片 |
| 想退回硬规则 worker-1 | 见上文「回滚到 worker-1」；本包已含 worker-1 tar |
| 想退回 0817 worker-2 | 停 worker-2，load 0817 的 worker-2 tar 或改 `.env` 回旧 tag |

---

## GPU 部署校验镜像

```bash
# 推荐：自动探测旧 tag
./scripts/retag-infer-images.sh --skip-lite-cpu

# 或显式指定
# ./scripts/retag-infer-images.sh --skip-lite-cpu 20260817-feature-eventworker2-0b26d8a
# ./scripts/retag-infer-images.sh --skip-lite-cpu 20260813-feature-eventworker2-5e4f4fe
# ./scripts/retag-infer-images.sh --skip-lite-cpu 20260727-test-from-4841de6a-85288b7

./verify-images.sh --skip-lite-cpu
```

说明：`visual-dps-inference-lite`（CPU）**不是 GPU 现场必需**；两命令的 `--skip-lite-cpu` 应同时使用。  
retag 详细用法见上文「推理镜像 retag」专节，或 `./scripts/retag-infer-images.sh --help`。

详细文件清单见 `app/deploy/PACKAGE-MANIFEST.md`（若存在）。

---

*构建：git `__GIT_COMMIT__` · tag `__TAG__` · __BUILD_DATE__*
