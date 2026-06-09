# Edge Node 接入说明书

> **分支**：`feat/edge-node-rest-ingest`（本特性开发分支）  
> **产品版本**：visual-dps 26.1.1  
> **文档状态**：目标架构 + 分阶段落地（§11 标明已实现 / 待实现）

---

## 1. 设计原则（已共识）

| 原则 | 说明 |
|------|------|
| 边缘 **只走 REST** | 禁止边缘直连 Redis；中心 API 内聚后再写 Redis |
| 鉴权 **Keycloak M2M** | Client Credentials（Trusted / Service Account Client） |
| 摄像头 / ROI **UI 配置** | 持久化仍为 `localdata/` JSON，经 **现有 API** 读写，无需手改文件 |
| 边缘接入形态 | `inference_mode=edge`，UI 禁止启中心推理容器 |
| 能力可组合 | `edge_capabilities`：`pose`、可选 `video`；未来可加 `collision` |
| 演进友好 | 先中心碰撞；算力富余时碰撞/AI 检测可下沉边缘，协议预留 `EventFrame` 上报 |

---

## 2. 目标拓扑（Phase A — 近期落地）

```
边缘 Node                              中心 visual-dps
┌─────────────────────┐                ┌──────────────────────────────────┐
│ Pose 推理（+ 可选视频) │                │ Keycloak（Client Credentials）    │
│                     │  HTTPS         │                                  │
│ POST .../pose       ├───────────────►│ POST /api/edge/v1/.../pose       │
│ Bearer access_token │                │   → 校验 token + camera 白名单    │
│                     │                │   → publish_pose_frame()（内网 Redis）│
│ （可选）RTSP 推/拉   ├─ RTSP ───────►│ MediaMTX（source_type 配置）       │
└─────────────────────┘                │ event-worker：碰撞 / Java 回调      │
                                       │ UI：SSE 骨架 + ROI 叠加            │
                                       └──────────────────────────────────┘
```

**边缘侧**：每帧 **1 次 HTTP**（或 batch）。  
**中心侧**：Redis 多写（stream + snapshot + pubsub）仅在 **UI 进程内** 完成，边缘不可见。

---

## 3. 未来拓扑（Phase B/C — 边缘算力富余）

### Phase B：边缘 Rule-based 碰撞

```
Edge: Pose + CollisionProcessor(本地 ROI 副本)
  ├─ POST /api/edge/v1/cameras/{id}/pose      （可选，UI 仍要骨架）
  └─ POST /api/edge/v1/cameras/{id}/events    （EventFrame，含 collisions / alarm）
Center: event-worker 对该路 inference_mode=edge_collision 时 **跳过碰撞**，仅：
  - 合并 overlay → SSE
  - Java 回调 / 审计（可配置「仅转发 edge 事件」）
```

ROI 同步：中心 UI 保存标注后，边缘通过 **`GET /api/edge/v1/cameras/{id}/annotation`**（只读，同 Keycloak）拉取或 Webhook 推送（P2）。

### Phase C：边缘 AI 检测（预留）

- 在 `EventFrame` 或独立 `kind: "detection"` 帧扩展字段，**不破坏** `schema: 1` pose/event。
- 中心角色：汇聚、展示、策略下发、回调；具体检测类型后续单独立项。

---

## 4. Keycloak 接入（OAuth2 Client Credentials）

### 4.1 Client 类型

在 Keycloak 为每类边缘节点（或每站点）建 **Confidential Client**，开启：

- **Client authentication**：ON  
- **Service accounts roles**：ON（Trusted Client / Service Account）  
- **Standard flow**：OFF（边缘不用浏览器登录）  
- **Direct access grants**：OFF  

Grant：`client_credentials`。

### 4.2 边缘取 Token

```http
POST {KEYCLOAK}/realms/{realm}/protocol/openid-connect/token
Content-Type: application/x-www-form-urlencoded

grant_type=client_credentials
&client_id=visual-dps-edge-230
&client_secret=***
```

响应 `access_token` 用于：

```http
POST https://{center}/api/edge/v1/cameras/edge-cam1/pose
Authorization: Bearer {access_token}
Content-Type: application/json
```

### 4.3 中心校验（待实现 · 低入侵）

**不做**全局「Keycloak JWT 中间件」，也不改 UI 的 Session / OAuth Code 流程。

| 层级 | 做法 |
|------|------|
| `AuthMiddleware` | 仅增加：`/api/edge/` ** bypass Session**（与 `/api/auth/` 类似，一行前缀放行） |
| Edge 路由 | `Depends(require_edge_bearer)`：**仅** edge 路由校验 `Authorization: Bearer` |
| 验签 | 依赖内 JWKS 验 JWT（issuer/aud 可配置，JWKS 缓存）；或可选 introspection |

与 UI 浏览器 SSO **完全分离**；未启用 edge API 时行为与现网一致。

### 4.4 配置项（规划）

`app_config.auth.keycloak` 或环境变量：

| 项 | 说明 |
|----|------|
| `issuer` | `https://keycloak/realms/xxx` |
| `jwks_uri` | JWKS 地址 |
| `audience` | 期望 aud（可选） |
| `edge_api_enabled` | 是否开放 edge ingest |

---

## 5. REST API

### 5.1 上报状态（**必做 · 已实现**）

画面长时间无人时 pose 不会更新，**不能**用 pose 推断 edge 是否存活。边缘应 **周期性**（建议 15～30s）调用：

```http
POST /api/edge/v1/cameras/{camera_id}/status
Authorization: Bearer …
Content-Type: application/json
```

**Body**：

```json
{
  "schema": 1,
  "ts": 1710000000.0,
  "state": "idle",
  "message": "无人体目标",
  "agent_version": "0.1.0",
  "metrics": { "fps": 5, "infer_ms": 12.3 }
}
```

| 字段 | 说明 |
|------|------|
| `state` | `running`（正常推流/推理）\| `idle`（画面中无人，pose 可不更新）\| `error` |
| `message` | 可选说明；`error` 时展示在 UI |
| `agent_version` | 边缘 agent 版本 |
| `metrics` | 可选指标 |

**中心存储**：Redis `edge:status:{camera_id}`（TTL `EDGE_STATUS_TTL_SEC`，默认 60s）。  
**UI**：`/api/cameras` 返回 `edge_runtime`（`agent_alive`、`display`、`state` 等）。  
**拓扑**：`EDGE_STATUS_STALE` = 超时未收到 status；`state=idle` 时不报 `POSE_STALE`。

### 5.2 上报 Pose（已实现）

```http
POST /api/edge/v1/cameras/{camera_id}/pose
Authorization: Bearer …
```

**Body**：与 §6 PoseFrame 相同。

**Response**：`{ "status": "accepted", ... }`

**中心内部**：`publish_pose_frame()`（Redis pipeline，边缘无感）。

### 5.3 批量 Pose（P1）

```http
POST /api/edge/v1/cameras/{camera_id}/pose/batch
Body: { "frames": [ PoseFrame, ... ] }
```

减少 HTTP 开销；中心逐帧 pipeline。

### 5.4 上报 Event（Phase B · 已实现）

```http
POST /api/edge/v1/cameras/{camera_id}/events
Authorization: Bearer …
```

Body：`EventFrame`（`docs/PIPELINE_SPLIT.md`）：

```json
{
  "schema": 1,
  "kind": "event",
  "ts": 1710000000.0,
  "frame_idx": 120,
  "collisions": ["shelf:box"],
  "alarm_collisions": ["shelf:box"],
  "skeletons": []
}
```

- 摄像头须 `edge_capabilities` 含 **`collision`**
- 中心 `publish_event_frame_dict()` → Redis；`alarm_collisions` 触发 Java 回调（与 central 一致）
- **event-worker 对该路 pose 跳过碰撞**（不破坏 central 路）

### 5.5 拉取 ROI 标注（Phase B · 已实现）

```http
GET /api/edge/v1/cameras/{camera_id}/annotation
Authorization: Bearer …
```

返回与 UI 标注 API 相同结构，另含 `revision` / `updated_at`（文件 mtime），供 edge 本地缓存与增量同步。

### 5.6 批量 Pose（P1）

与 §5.3 相同，待实现。

---

## 6. 数据契约

### 6.1 PoseFrame（不变）

与 `services/pose_bus.build_pose_frame`、`docs/PIPELINE_SPLIT.md` 一致：

```json
{
  "schema": 1,
  "kind": "pose",
  "ts": 1710000000.123,
  "camera_id": "edge-cam1",
  "frame_idx": 120,
  "infer_width": 640,
  "infer_height": 360,
  "persons": [{ "person_id": 0, "keypoints": [[x, y, score], ...] }]
}
```

- **COCO-17** 关键点；碰撞至少 11 点。  
- `infer_width/height` 与坐标一致，供 ROI 缩放。

### 6.2 EventFrame（Phase B 边缘碰撞）

```json
{
  "schema": 1,
  "kind": "event",
  "ts": 1710000000.123,
  "camera_id": "edge-cam1",
  "frame_idx": 120,
  "collisions": ["shelf:box"],
  "alarm_collisions": ["shelf:box"],
  "skeletons": []
}
```

可与 pose 分开发；UI LiveHub 仍按现有逻辑合并 pose + event。

---

## 7. 摄像头配置（UI / API，非必须手改 JSON）

### 7.1 存储

- 列表：`localdata/camera_ips.json`（`camera_store.py`）  
- ROI：`localdata/json/cameras/{camera_id}.json`  

**读写均已有 HTTP API**（设置页 / 标注页）：

| 操作 | API |
|------|-----|
| 列表 / 新建 / 改 / 删 | `GET/POST /api/cameras`、`PUT/DELETE /api/cameras/{id}` |
| ROI 标定 | `GET/POST /api/cameras/{id}/annotation` |
| MediaMTX 同步 | `POST /api/cameras/apply_mediamtx` |

暂无独立业务 DB；后续若迁库，Repository 抽象即可，**不影响 edge 协议**。

### 7.2 字段扩展（待实现 · **必须兼容旧数据**）

现有 `camera_ips.json`（cam2～cam8 等）**无新字段也能跑**：代码侧默认 `inference_mode=central`，UI / 推理 / 拓扑行为与 today 一致。

| 字段 | 缺省 | 说明 |
|------|------|------|
| `inference_mode` | `"central"` | 仅显式设为 `edge` 时走 REST ingest、禁中心 infer |
| `edge_capabilities` | `[]` 或省略 | 仅 `inference_mode=edge` 时有意义 |

**实现注意**：`camera_store._normalize_record`  today 会丢弃未知键；落地时需 **透传** 上述可选字段，且 save 时不 strip 旧 camera 的其它已有字段。

边缘路示例（新加路时使用，非改旧路）：

```json
{
  "id": "edge-cam1",
  "name": "230 边缘摄像头1",
  "path": "edge-cam1",
  "url": "rtsp://192.168.1.230:8554/live0",
  "source_type": "external",
  "inference_mode": "edge",
  "edge_capabilities": ["pose"],
  "enabled": true,
  "settings": { "inference.frame_rate": 5, "inference.height": 360 }
}
```

| 字段 | 说明 |
|------|------|
| `inference_mode` | `central`（默认）\| `edge` \| `disabled` |
| `edge_capabilities` | `["pose"]` \| `["pose","video"]` \| `["pose","collision"]`（Phase B） |
| `source_type` | 与 **视频** 如何进中心有关，与 pose 来源正交 |

**`edge_capabilities` 组合**

| 能力 | 边缘行为 | 中心行为 |
|------|----------|----------|
| 仅 `pose` | REST 推 pose | 碰撞在中心 event-worker |
| `pose` + `video` | REST + RTSP（publisher / 被 pull） | MediaMTX 出画 |
| `pose` + `collision`（未来） | REST pose + REST event | 跳过中心碰撞，只做汇聚/回调 |

### 7.3 UI 行为（待实现）

- `inference_mode=edge`：**隐藏或禁用「启动推理」**  
- 拓扑：`POSE_STALE` 改看 **edge pose API / Redis snapshot**，不再依赖 `visual-dps-infer-*`  

---

## 8. Redis 写入优化（中心内部）

边缘 **不再**直连 Redis。中心 `publish_pose_frame()` 现状：

| 写 | 消费者 |
|----|--------|
| `XADD pose:stream` | event-worker |
| `SET pose:snapshot:{cam}` | UI 首包 / 拓扑 |
| `PUBLISH pose:live:{cam}` | LiveHub SSE |

**P0**：保持语义，已用 **pipeline 一次 RTT**；REST 入口单次调用即可。  
**P2（可选）**：LiveHub 改订阅 stream 或合并写，减少 pubsub；需评估，非 edge 阻塞项。

**禁止**：为省写而去掉 `XADD`（`POSE_DELIVERY=stream` 下中心碰撞会断）。

---

## 9. 中心部署要点

```bash
docker compose up -d redis mediamtx visual-dps-ui visual-dps-event-worker
```

- **Redis 不对边缘暴露**（仅 compose 内网）。  
- Edge 只需 HTTPS 到 `UI_PORT`（默认 8045）及 Keycloak。  
- 验证：`curl -s http://127.0.0.1:8045/api/version`

---

## 10. 联调检查清单（REST 目标态）

### 10.1 Keycloak

- [ ] Client Credentials 能拿到 `access_token`  
- [ ] Token 带预期 audience / roles  

### 10.2 中心配置（UI）

- [ ] 已创建 camera，`inference_mode=edge`  
- [ ] ROI 已在标注页保存（若要中心碰撞）  
- [ ] 未启动中心推理容器  

### 10.3 Edge → Center

- [ ] `POST .../pose` 返回 202  
- [ ] `GET pose:snapshot:{cam}`（中心内 redis-cli）持续更新  
- [ ] 监控页 SSE 有骨架  
- [ ] 碰框 / Java 回调（中心碰撞 Phase A；边缘碰撞 Phase B 走 event API）  

---

## 11. 实施路线图

| 阶段 | 内容 | 状态 |
|------|------|------|
| **P0** | 分支 `feat/edge-node-rest-ingest` | ✅ 已建 |
| **P0** | `POST /api/edge/v1/.../status` + Redis + UI `edge_runtime` | ✅ 已实现 |
| **P0** | `POST /api/edge/v1/.../pose` + Keycloak JWT 校验 | ✅ 已实现 |
| **P0** | `inference_mode` + UI 禁 infer + 拓扑适配 | ✅ 已实现 |
| **P0** | `publish_pose_frame` 作为唯一 Redis 入口 | 已有函数，接 API 即可 |
| **P1** | pose batch、限流、审计日志 | 待开发 |
| **P1** | Keycloak 配置文档 / `.env.example` 样例 | 待开发 |
| **P2** | `POST .../events` + 边缘碰撞模式 | ✅ 已实现 |
| **P2** | ROI 同步 `GET .../annotation` | ✅ 已实现 |
| **P3** | 边缘 AI 检测扩展帧 | 预留 |

---

## 12. 源码索引

| 模块 | 路径 |
|------|------|
| Pose 发布（中心内） | `services/pose_bus.py` |
| 管道契约 | `docs/PIPELINE_SPLIT.md` |
| 事件消费 | `services/event_engine/worker.py` |
| 碰撞算法 | `services/event_engine/collision.py` |
| UI SSE | `services/live_bus.py` |
| 摄像头 CRUD | `services/camera_routes.py`、`services/camera_store.py` |
| 标注 | `services/annotation_service.py` |
| 认证（UI SSO） | `services/auth_routes.py`、`core/auth_middleware.py` |

---

## 附录：当前版本（改造前）临时说明

在 **P0 API 未合并前**，若需联调管道，可在中心内网用 Redis 模拟（**仅限开发**），见 git 历史中 v1 文档或 `pose_bus.publish_pose_frame`。  
**生产边缘必须以 REST + Keycloak 为准**，不得持有 `REDIS_PASSWORD`。
