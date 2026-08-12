# event-worker-2 生产部署与测试说明

给现场同事：在**已有 Visual-DPS 生产栈**上切换/对照 **pick_state** 事件 worker（`visual-dps-event-worker-2`）。

| 项 | 说明 |
|----|------|
| 分支 | `exp/event-worker-2`（HQIT/visual-dps） |
| 与 worker-1 关系 | **备选**，不是并行双开 |
| 共用 | Redis `pose:stream`、消费组 `event-workers` |
| 算法配置 | 镜像内 `pick_state/configs/pipeline.v5_gated.json`（含动作门控 A / 邻框门控 B） |

**硬性约定：同一时刻只跑一个 event-worker。**  
两个都挂着会抢同一 group，各只吃到一部分 pose，表现为漏检。

---

## 1. 前置条件

1. 生产机已按离线包跑通：`redis` / `mediamtx` / `visual-dps-ui` / `visual-dps-event-worker` / 推理容器  
2. 工作目录一般是部署包下的 `app/`（含 `docker-compose.deploy.yml`、`.env`、`localdata/`）  
3. 本机可 `docker` / `docker compose`（或 `docker-compose`）  
4. 已拿到 **event-worker-2 镜像**（见 §2），tag 与现场 `VISUAL_DPS_IMAGE_TAG` 一致或可改 `.env`

代码/配置来源（二选一）：

- 从研发同步更新后的 `app/`（含 `Dockerfile.event-worker-2`、`pick_state/`、`event_worker_2.py`、compose 里的 `visual-dps-event-worker-2`）  
- 或只接收对方提供的 `docker save` 镜像 tar + 本说明

---

## 2. 准备镜像

### 2.1 有源码时本地构建

```bash
cd <部署包>/app   # 或含 Dockerfile.event-worker-2 的工程根
set -a && source .env && set +a

# tag 建议与现网其它镜像一致，例如:
# export VISUAL_DPS_IMAGE_TAG=20260727-test-from-4841de6a-85288b7

docker compose --profile worker-2 build visual-dps-event-worker-2
# 若现场只用 deploy 文件、无 build 段：
# docker build -f Dockerfile.event-worker-2 -t visual-dps-event-worker-2:${VISUAL_DPS_IMAGE_TAG} .
```

确认：

```bash
docker images 'visual-dps-event-worker-2*'
```

### 2.2 离线 load

```bash
docker load -i visual-dps-event-worker-2-<tag>.tar
# 若 tag 与 .env 不一致，可改名：
# docker tag visual-dps-event-worker-2:<旧> visual-dps-event-worker-2:${VISUAL_DPS_IMAGE_TAG}
```

---

## 3. 切换到 worker-2（生产对照）

在 **`app/`** 目录执行（compose 文件按现场实际二选一）。

### 3.1 停硬规则 worker

```bash
docker compose -f docker-compose.deploy.yml stop visual-dps-event-worker
# 若现场用 docker-compose.yml：
# docker compose stop visual-dps-event-worker
```

确认已停：

```bash
docker ps -a --filter name=visual-dps-event-worker --format '{{.Names}} {{.Status}}'
# 应只有 Exited / 无 Running 的 visual-dps-event-worker（不要同时有 worker-2 以外的 Running）
```

### 3.2 启动 worker-2

```bash
docker compose -f docker-compose.deploy.yml --profile worker-2 up -d visual-dps-event-worker-2
```

常用环境变量（已在 compose 里，可按需在 `.env` 覆盖）：

| 变量 | 默认 | 说明 |
|------|------|------|
| `POSE_STREAM_KEY` | `pose:stream` | 与推理一致 |
| `POSE_STREAM_GROUP` | `event-workers` | 与现网一致 |
| `PICK_STATE_CONFIG` | `pick_state/configs/pipeline.v5_gated.json` | 生产 gated |
| `EVENT_WORKER_ENABLE_CALLBACKS` | `1` | `0` 则只出 event、不打 Java |
| `COLLISION_LOG` | `0` | `1` 打开碰撞相关日志 |
| `JSON_DIR` | `/app/localdata/json` | 货框标注，与现网相同挂载 |

### 3.3 看启动日志

```bash
docker logs -f --tail=80 visual-dps-event-worker-2
```

期望类似：

```text
ℹ️ Event worker-2 已启动 delivery=stream pick_state=... key=pose:stream group=event-workers ...
```

有报错（缺模型、Redis 认证失败、标注路径不对）先不要开检测对照。

---

## 4. 生产侧怎么测

### 4.1 功能冒烟（真实摄像头）

1. UI 确认对应路 **智能检测已开**（推理在推 `pose:stream`）  
2. 现场做几次真实拣货 / 故意干扰动作  
3. 观察：
   - UI 监控叠加 / 告警是否出现  
   - Java 回调是否到达（`EVENT_WORKER_ENABLE_CALLBACKS=1` 时）  
   - `docker logs visual-dps-event-worker-2` 是否有消费与告警相关输出  

建议先开 1～2 路摄像头对照，再扩大。

### 4.2 看 Redis 事件快照（可选）

在能访问 Redis 的机器上（密码见 `.env` 的 `REDIS_PASSWORD`）：

```bash
# 进 redis 容器示例
docker exec -it visual-dps-redis redis-cli -a "$REDIS_PASSWORD" --no-auth-warning
GET event:snapshot:<camera_id>
```

JSON 中关注 `collisions` / `alarm_collisions`（货框 token，如 `Box_1001` 或 `货架:框号`）。

### 4.3 不碰现网流的合成冒烟（可选，研发机已验证）

用**独立** stream，避免影响现网 group：

```bash
# 详见 scripts/smoke_event_worker_2_redis.py 与 docs/DAILY-2026-08-12.md
# 要点：POSE_STREAM_KEY=pose:stream:ew2-smoke + pipeline.v5_smoke.json
```

现场生产对照一般用 §4.1，不必走合成流。

---

## 5. 回滚到硬规则 worker-1

```bash
docker compose -f docker-compose.deploy.yml --profile worker-2 stop visual-dps-event-worker-2
# 或: docker rm -f visual-dps-event-worker-2

docker compose -f docker-compose.deploy.yml start visual-dps-event-worker
# 若容器已被删：
# docker compose -f docker-compose.deploy.yml up -d visual-dps-event-worker
```

确认：

```bash
docker ps --filter name=visual-dps-event-worker --format '{{.Names}} {{.Status}}'
docker logs --tail=30 visual-dps-event-worker
```

只应有 **`visual-dps-event-worker` Running**，没有 worker-2。

---

## 6. 常见问题

| 现象 | 处理 |
|------|------|
| 告警变少/漏一半 | 检查是否 **worker-1 与 worker-2 同时 Running** → 停掉其中一个 |
| worker-2 起不来 / Image not found | §2 构建或 `docker load`；核对 `.env` 的 `VISUAL_DPS_IMAGE_TAG` |
| 一直 `no boxes for camera=` | 检查 `localdata/json/cameras/<id>.json` 是否存在且挂载进容器 |
| 有 pose 无告警 | 默认 gated 会压误报；可临时 `COLLISION_LOG=1` 看日志；或与研发确认是否先改阈值/关 B |
| 只要事件不要回调 | `-e EVENT_WORKER_ENABLE_CALLBACKS=0` 后重建/更新容器 |
| compose profile 不生效 | 必须带 `--profile worker-2`；旧版 `docker-compose` 同样支持 |

---

## 7. 与研发的分工

| 角色 | 事项 |
|------|------|
| 研发 | 镜像 / `app` 增量、本说明、冒烟脚本 |
| 现场 | 停 1 启 2、开检测对照、记录漏报/误报样例、出问题回滚 §5 |
| 联调记录 | 摄像头 ID、时段、是否开回调、worker-2 镜像 tag、异常日志片段 |

问题反馈请附：`docker logs visual-dps-event-worker-2 --tail=200` 与 `docker ps` 截图/文本。
