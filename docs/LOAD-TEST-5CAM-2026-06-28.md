# 5 路推理负载测试记录（2026-06-28）

> 服务器：**192.168.1.153** · UI 端口 **8046** · 镜像 tag `20260628-test-from-4841de6a-56f47b1`  
> 推流源：WSL `/home/rzy/multi-samples.mp4` → `rtsp://192.168.1.153:8554/cam1`～`cam5`  
> 背景：在 12 路压测后（event-worker `lag≈2000`、碰撞 overlay 滞后 ~15s），缩减为 5 路复测 worker 与前端碰撞实时性。

---

## 一、测试目标

| 项 | 内容 |
|----|------|
| 推流 | WSL ffmpeg 循环推同一 MP4 至 **5** 路 MediaMTX path（cam1～cam5） |
| 推理 | 清除全部 infer 后，仅启动 `visual-dps-infer-cam1`～`cam5` |
| 观测 | GPU/内存、Redis pose 积压、MediaMTX ready、单路 fps、event-worker lag |

---

## 二、环境与配置快照

### 2.1 硬件（153）

| 资源 | 值 |
|------|-----|
| GPU | NVIDIA GeForce RTX 4090 · 24564 MiB |
| 内存 | 31 GiB |

### 2.2 `app_config.json` 推理相关

| 参数 | 值 |
|------|-----|
| `frame_rate` | 15 |
| `pose_frame_interval` | 1 |
| `height` | 480 |
| `alarm_min_consecutive_frames` | 7 |
| 默认 backend | `rtmpose_t` |

### 2.3 各摄像头 backend（`camera_ips.json` settings）

| 路 | backend |
|----|---------|
| cam4 | `rtmpose_t` |
| cam1, cam2, cam3, cam5 | 默认 → 实际拉起 **`rtmpose_m`** |

### 2.4 推流侧（WSL，测试前）

| path | ready | source |
|------|-------|--------|
| cam1～cam5 | **5/5 True** | rtspSession |
| cam6～cam12 | False | —（已停推） |

---

## 三、执行过程（153）

### 3.1 清除全部推理容器

```bash
docker exec visual-dps-ui python3 -c "
import time
from services.inference_container_service import stop_inference_container

ids = [f'cam{i}' for i in range(1, 13)] + ['test_camera']
for cid in ids:
    r = stop_inference_container(cid)
    print(cid, r.get('status'))
    time.sleep(0.2)
"
```

**结果：** cam1～cam12、test_camera 全部 `success` 停止；无残留 running infer（cam6～12、test_camera 已退出）。

### 3.2 启动 5 路推理（间隔 3s）

```bash
docker exec visual-dps-ui python3 -c "
import json, time
from services.camera_store import load_cameras
from services.inference_container_service import start_inference_container

items = {c['id']: c for c in load_cameras('/app/localdata/camera_ips.json')}
results = []
for cid in [f'cam{i}' for i in range(1, 6)]:
    r = start_inference_container(items[cid])
    results.append({
        'camera': cid,
        'status': r.get('status'),
        'backend': (r.get('inference') or {}).get('backend'),
    })
    time.sleep(3)
print(json.dumps(results, ensure_ascii=False))
"
```

**启动结果（5/5）：**

```json
[
  {"camera": "cam1", "status": "success", "backend": "rtmpose_m"},
  {"camera": "cam2", "status": "success", "backend": "rtmpose_m"},
  {"camera": "cam3", "status": "success", "backend": "rtmpose_m"},
  {"camera": "cam4", "status": "success", "backend": "rtmpose_t"},
  {"camera": "cam5", "status": "success", "backend": "rtmpose_m"}
]
```

### 3.3 预热后采集（启动后 **90s**，采集时刻 **2026-06-28 19:45:24**）

---

## 四、测试结果 ✅

### 4.1 是否完成有效负载测试？

**是。** 5/5 推流 ready、5/5 推理持续 running，Redis **lag=0**，event 与 pose **基本同步**（见 §4.4）。

### 4.2 MediaMTX

| path | ready | source |
|------|-------|--------|
| cam1 | True | rtspSession |
| cam2 | True | rtspSession |
| cam3 | True | rtspSession |
| cam4 | True | rtspSession |
| cam5 | True | rtspSession |

**合计：5/5 ready**

### 4.3 推理容器

| 项 | 结果 |
|----|------|
| running | **5/5** `Up` |
| exited | **0** |
| status.json | 5/5 `state=running`, `is_inferencing=true` |

| 路 | backend | CPU | 内存 |
|----|---------|-----|------|
| cam1 | rtmpose_m | 61.2% | 804.7 MiB |
| cam2 | rtmpose_m | 56.3% | 802.8 MiB |
| cam3 | rtmpose_m | 56.5% | 808.2 MiB |
| cam4 | rtmpose_t | 54.7% | 769.1 MiB |
| cam5 | rtmpose_m | 57.1% | 804.8 MiB |

### 4.4 资源与 Redis

| 指标 | 实测 |
|------|------|
| **GPU 利用率** | **41%** |
| **GPU 显存** | **5812 / 24564 MiB**（约 24%） |
| **系统内存** | **18 / 31 GiB used**（available ~12 GiB） |
| Redis `pose:stream` XLEN | 2000（maxlen 顶格，正常） |
| Redis consumer **lag** | **0** |
| Redis pending | 0 |

### 4.5 吞吐（5s 窗口 `frame_idx` 增量）

| 路 | backend | 估算 fps | pose vs event frame 差 |
|----|---------|----------|------------------------|
| cam1 | rtmpose_m | 17.0 | -10（event 略超前） |
| cam2 | rtmpose_m | 16.8 | -9 |
| cam3 | rtmpose_m | 16.6 | -9 |
| cam4 | rtmpose_t | 16.6 | -9 |
| cam5 | rtmpose_m | 16.6 | -10 |
| **合计** | — | **83.6 fps** | worker **无积压** |

> 配置目标 15 fps/路，5 路理论 **75 fps**；实测 **83.6 fps（≈111%）**，单路 **16.6～17.0 fps**。  
> pose 发布延迟：median **41 ms**（min 22 / max 83 ms）。

### 4.6 结论

| 项 | 结论 |
|----|------|
| 5 路并行推理 | ✅ **通过** |
| event-worker 消费 | ✅ **lag=0**，碰撞 overlay 应与骨架基本同步 |
| 瓶颈 | 未触顶（GPU 41%）；12 路时 lag≈2000 的问题在 5 路下消失 |
| 对比 12 路 | GPU 80%→41%，合计 fps 136→84，**worker lag 2000→0** |

---

## 五、与 12 路压测对比

| 指标 | 12 路（§八 第二轮） | 5 路（本次） |
|------|---------------------|--------------|
| MediaMTX ready | 12/12 | **5/5** |
| infer running | 12 | **5** |
| GPU 利用率 | 80% | **41%** |
| GPU 显存 | 13244 MiB | **5812 MiB** |
| Redis lag | **2000** | **0** |
| 单路 fps | 10.2～12.6 | **16.6～17.0** |
| 合计 pose 吞吐 | 136 fps | **83.6 fps** |
| 碰撞 overlay | 滞后 ~15s | **实时（lag=0）** |

---

## 六、WSL 推流命令（5 路）

### 6.1 停止 12 路（若仍在推）

```bash
pkill -f "ffmpeg.*192.168.1.153:8554"
```

### 6.2 启动 cam1～cam5

```bash
VIDEO=/home/rzy/multi-samples.mp4
SERVER=192.168.1.153
PORT=8554

for name in cam1 cam2 cam3 cam4 cam5; do
  nohup ffmpeg -hide_banner -loglevel warning -re -stream_loop -1 \
    -i "${VIDEO}" \
    -vf "scale=640:480:force_original_aspect_ratio=decrease,pad=640:480:(ow-iw)/2:(oh-ih)/2" \
    -r 15 -c:v libx264 -pix_fmt yuv420p -preset ultrafast -tune zerolatency \
    -b:v 800k -maxrate 800k -bufsize 1600k -g 15 \
    -f rtsp -rtsp_transport tcp \
    "rtsp://${SERVER}:${PORT}/${name}" \
    > "/tmp/ffmpeg-${name}.log" 2>&1 &
  sleep 0.5
done
```

### 6.3 153：验证 ready 后再启推理

```bash
curl -s http://127.0.0.1:9997/v3/paths/list | python3 -c "
import json,sys
d=json.load(sys.stdin)
for x in sorted(d['items'], key=lambda i: i['name']):
    if x['name'] in ('cam1','cam2','cam3','cam4','cam5'):
        print(x['name'], x.get('ready'), (x.get('source') or {}).get('type'))
"
```

### 6.4 停止 5 路推流

```bash
pkill -f "ffmpeg.*192.168.1.153:8554/cam[1-5]"
```

### 6.5 停止 5 路推理

```bash
for i in $(seq 1 5); do
  docker exec visual-dps-ui python3 -c "
from services.inference_container_service import stop_inference_container
stop_inference_container('cam${i}')
"
done
```

---

## 七、注意事项

1. **勿在推流/推理期间 restart mediamtx**（见 [LOAD-TEST-12CAM-2026-06-28.md](./LOAD-TEST-12CAM-2026-06-28.md) §四）。
2. 12 路时单 event-worker 跟不上 **~136 fps** pose 写入，导致 `lag≈2000`、前端碰撞滞后；**5 路 ~84 fps 时 lag=0**。
3. 若需 12 路且碰撞实时：需扩展 event-worker（多副本 / 批量消费 / 跳帧策略）。

---

## 八、相关文件

| 文件 | 说明 |
|------|------|
| [LOAD-TEST-12CAM-2026-06-28.md](./LOAD-TEST-12CAM-2026-06-28.md) | 12 路压测与 worker 滞后分析 |
| `localdata/camera_ips.json` | cam1～cam5 配置 |
| `app_config.json` | 全局推理参数 |
| `scripts/probe-pipeline-latency.py` | 单路 pose / worker lag 探测 |
