# 12 路推理负载测试记录（2026-06-28）

> 服务器：**192.168.1.153** · UI 端口 **8046** · 镜像 tag `20260628-test-from-4841de6a-56f47b1`  
> 推流源：WSL `/home/rzy/multi-samples.mp4` → `rtsp://192.168.1.153:8554/cam*`

---

## 一、测试目标

| 项 | 内容 |
|----|------|
| 推流 | WSL ffmpeg 循环推同一 MP4 至 12 路 MediaMTX path |
| 推理 | 同时启动 `visual-dps-infer-cam1` … `cam12` 共 12 容器 |
| 观测 | GPU/内存、Redis pose 积压、MediaMTX ready、推理状态 |

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

### 2.3 各摄像头 backend（来自 `camera_ips.json` settings）

| 路 | backend |
|----|---------|
| cam4, cam6 | `rtmpose_t` |
| 其余 cam1～cam12 | 默认 → 实际拉起为 **`rtmpose_m`**（GPU ONNX） |

### 2.4 推流侧（WSL 实测，测试前）

```text
cam1～cam10、cam12  ready=True  source=rtspSession  （11 路 WSL 推流成功）
cam11              ready=False source=rtspSource   （当时 mediamtx 仍为 149 拉流）
```

后已将 `camera_ips` 中 **cam11** 改为 `external`，并 regenerate `mediamtx.yml` → `source: publisher`（**需 WSL 补推 cam11**）。

---

## 三、执行过程（153 上实际操作）

### 3.1 同步 MediaMTX（cam11 → publisher）

```bash
cd /home/hqit/workspace/visual-dps
docker run --rm \
  -v "$(pwd)/localdata:/app/localdata" \
  -v "$(pwd)/app_config.json:/app/app_config.json:ro" \
  --env-file .env \
  -e MEDIAMTX_CONFIG_PATH=/app/localdata/mediamtx.yml \
  -e CAMERA_IPS_FILE=/app/localdata/camera_ips.json \
  visual-dps-visual-dps-ui:20260628-test-from-4841de6a-56f47b1 \
  python3 -c "from services.camera_store import apply_mediamtx; apply_mediamtx('/app/localdata/camera_ips.json','/app/localdata/mediamtx.yml')"

docker restart visual-dps-mediamtx   # ⚠ 见第四节：此步导致 WSL 推流全部断开
```

### 3.2 批量启停 12 路推理（间隔 3s 错开冷启动）

```bash
docker exec visual-dps-ui python3 -c "
import json, time
from services.camera_store import load_cameras
from services.inference_container_service import stop_inference_container, start_inference_container

CAMS = [f'cam{i}' for i in range(1, 13)]
items = {c['id']: c for c in load_cameras('/app/localdata/camera_ips.json')}
for cid in CAMS:
    if cid in items: stop_inference_container(cid)
time.sleep(2)
results = []
for cid in CAMS:
    r = start_inference_container(items[cid])
    results.append({'camera': cid, 'status': r.get('status'), 'backend': (r.get('inference') or {}).get('backend')})
    time.sleep(3)
print(json.dumps(results, ensure_ascii=False))
"
```

**启动结果（12/12 容器创建成功）：**

```json
[
  {"camera":"cam1","status":"success","backend":"rtmpose_m"},
  {"camera":"cam2","status":"success","backend":"rtmpose_m"},
  {"camera":"cam3","status":"success","backend":"rtmpose_m"},
  {"camera":"cam4","status":"success","backend":"rtmpose_t"},
  {"camera":"cam5","status":"success","backend":"rtmpose_m"},
  {"camera":"cam6","status":"success","backend":"rtmpose_t"},
  {"camera":"cam7","status":"success","backend":"rtmpose_m"},
  {"camera":"cam8","status":"success","backend":"rtmpose_m"},
  {"camera":"cam9","status":"success","backend":"rtmpose_m"},
  {"camera":"cam10","status":"success","backend":"rtmpose_m"},
  {"camera":"cam11","status":"success","backend":"rtmpose_m"},
  {"camera":"cam12","status":"success","backend":"rtmpose_m"}
]
```

### 3.3 预热后采集（约 75s）

使用内联 Python 采集 GPU、Redis、MediaMTX、status、infer 日志等（见仓库可复用 `scripts/probe-pipeline-latency.py` 单路延迟探测）。

---

## 四、测试结果与结论

### 4.1 本次是否完成有效负载测试？

**第一轮（19:09）：否。** 推理容器均在启动后 **数十秒内退出**，未形成 12 路持续推理负载（见 §4.2～§4.3）。

**第二轮（19:14）：是。** WSL 重推后按 §5 复测，12/12 推理持续运行，GPU 80%、合计 **136 fps**；完整指标见 **§八**。

### 4.2 第一轮根因

1. **步骤 3.1 中 `docker restart visual-dps-mediamtx`**  
   Publisher 推流在 MediaMTX 重启后 **全部断开**（WSL ffmpeg 不会自动重连）。  
   采集时：`ready 0/12`，所有 path 无流。

2. **推理容器 RTSP DESCRIBE 404**  
   日志共性：

   ```text
   method DESCRIBE failed: 404 Not Found
   ⚠️ [警告] 无法读取当前视频分辨率，停止本次推理
   ```

3. **时序错误**  
   正确顺序应为：**WSL 推流 → 验证 ready → 启动推理**；  
   不应在推流进行中 restart mediamtx（除非 WSL 侧已准备立即重推）。

### 4.3 第一轮采集快照（2026-06-28 19:09:53）

| 指标 | 值 |
|------|-----|
| MediaMTX cam ready | **0 / 12** |
| 推理容器 running | **0**（12 个 Exited(0)） |
| infer status | 全部 `stopped` / `正常退出` |
| Redis `pose:stream` XLEN | 2001 |
| Redis consumer `lag` | **0** |
| GPU 利用率 | **0%**（无持续推理） |
| GPU 显存已用 | ~6200 MiB（多为其他进程/残留） |

### 4.4 已验证可行的部分

| 项 | 结果 |
|----|------|
| UI API 批量拉起 12 infer 容器 | ✅ 成功 |
| 镜像 / GPU 运行时 / Docker 网络 | ✅ 容器能创建并加载模型 |
| WSL → 153 推流 | ✅ 12/12 ready（第二轮） |
| 12 路 simultaneous 持续推理 + GPU 压测 | ✅ **第二轮完成**（§八） |

---

## 五、正确复测流程（推荐）

### 5.1 WSL：12 路推流（cam11 已改为 publisher 后）

```bash
VIDEO=/home/rzy/multi-samples.mp4
SERVER=192.168.1.153
PORT=8554

for name in cam1 cam2 cam3 cam4 cam5 cam6 cam7 cam8 cam9 cam10 cam11 cam12; do
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

### 5.2 153：验证 12 路 ready（必须 12/12 再启推理）

```bash
curl -s http://127.0.0.1:9997/v3/paths/list | python3 -c "
import json,sys
d=json.load(sys.stdin)
cams=[x for x in d['items'] if x.get('name','').startswith('cam') and len(x['name'])<=5]
ok=sum(1 for x in cams if x.get('ready'))
print(f'ready {ok}/{len(cams)}')
for x in sorted(cams,key=lambda i:i['name']):
    print(x['name'], x.get('ready'), (x.get('source') or {}).get('type'))
"
```

### 5.3 153：启动 12 路推理（勿再 restart mediamtx）

使用 **§3.2** 同一命令。

### 5.4 153：负载观测（推理稳定运行 2～5 min 后）

```bash
# 容器与 GPU
docker ps --format 'table {{.Names}}\t{{.Status}}' | grep infer-cam
nvidia-smi
docker stats --no-stream | grep infer-cam

# Redis 积压
docker exec visual-dps-redis redis-cli -a visual-dps-local XLEN pose:stream
docker exec visual-dps-redis redis-cli -a visual-dps-local XINFO GROUPS pose:stream

# 单路延迟（改 camera_id）
python3 scripts/probe-pipeline-latency.py

# 推理状态
ls localdata/inference/cam*.status.json
grep '"state"' localdata/inference/cam*.status.json
```

### 5.5 停止

```bash
# WSL
pkill -f "ffmpeg.*192.168.1.153:8554"

# 153 停推理
for i in $(seq 1 12); do
  docker exec visual-dps-ui python3 -c "
from services.inference_container_service import stop_inference_container
stop_inference_container('cam${i}')
"
done
```

---

## 六、注意事项

1. **restart mediamtx = 推流全断**，WSL 需重推或写自动重连脚本。  
2. **cam11** 必须与 `mediamtx.yml` 一致为 `publisher` 且 WSL 推 `cam11`。  
3. 12 路 **`rtmpose_m` GPU** 对 4090 压力较大，若 OOM 可改为全 `rtmpose_t` 或降低 `height`。  
4. 每路需有标注 JSON（缺省时复制 `precise_boxes_new.json`）。  
5. **勿在推流/推理期间改 mediamtx**；改 `camera_ips` 后 apply 配置会 restart，需协调 WSL 重推。

---

## 七、相关文件

| 文件 | 说明 |
|------|------|
| `localdata/camera_ips.json` | 12 路 cam1～cam12 + test_camera |
| `localdata/mediamtx.yml` | path / publisher 配置 |
| `app_config.json` | 全局推理与告警参数 |
| `scripts/probe-pipeline-latency.py` | 单路 pose 延迟探测 |

---

## 八、第二轮有效负载测试（2026-06-28 19:14:31）✅

WSL 重新推流后，**未 restart mediamtx**，按 §5.2 验证 ready → §5.3 启动推理 → 预热 90s 采集。

### 8.1 前置条件

| 项 | 结果 |
|----|------|
| MediaMTX ready | **12/12**（全部 `rtspSession`） |
| 推流源 | WSL `/home/rzy/multi-samples.mp4` → `rtsp://192.168.1.153:8554/cam*` |
| 推流负载位置 | **WSL**（12× ffmpeg 编码）；153 仅收流 |

### 8.2 推理容器

| 项 | 结果 |
|----|------|
| 启动 | 12/12 `success` |
| 运行中（预热 90s 后） | **12/12** `Up` |
| status.json | 12/12 `state=running`, `is_inferencing=true` |
| backend | cam4/cam6=`rtmpose_t`，其余 10 路=`rtmpose_m` |
| 已退出容器 | 无 |

### 8.3 资源占用（稳态采样）

| 指标 | 实测 |
|------|------|
| **GPU 利用率** | **80%** |
| **GPU 显存** | **13244 / 24564 MiB**（约 54%） |
| **系统内存** | **25 / 31 GiB used**（available ~5.2 GiB） |
| 单容器 CPU | 约 53%～66% |
| 单容器内存 | 约 768～812 MiB |
| Redis `pose:stream` XLEN | 2003（触顶 maxlen≈2000，正常） |
| Redis consumer | consumers=39, pending=1, **lag=0** |

**docker stats 快照（节选）：**

```text
visual-dps-infer-cam1   57.43%  805.7MiB
visual-dps-infer-cam2   62.29%  809.8MiB
visual-dps-infer-cam3   65.60%  802.7MiB
visual-dps-infer-cam4   55.80%  767.8MiB   (rtmpose_t)
visual-dps-infer-cam5   57.35%  804.8MiB
visual-dps-infer-cam6   56.47%  768.9MiB   (rtmpose_t)
visual-dps-infer-cam7   54.07%  807.9MiB
visual-dps-infer-cam8   60.21%  805MiB
visual-dps-infer-cam9   55.50%  812.4MiB
visual-dps-infer-cam10  53.22%  804.4MiB
visual-dps-infer-cam11  59.37%  806.7MiB
visual-dps-infer-cam12  55.06%  802.3MiB
```

### 8.4 吞吐与延迟（5s 窗口 `frame_idx` 增量）

| 路 | backend | 估算 fps | pose 发布延迟 (ms) |
|----|---------|----------|-------------------|
| cam1 | rtmpose_m | 11.2 | 115 |
| cam2 | rtmpose_m | 12.0 | 78 |
| cam3 | rtmpose_m | 10.2 | 117 |
| cam4 | rtmpose_t | 12.2 | 89 |
| cam5 | rtmpose_m | 11.2 | 108 |
| cam6 | rtmpose_t | 11.6 | 86 |
| cam7 | rtmpose_m | 11.4 | 91 |
| cam8 | rtmpose_m | 10.8 | 138 |
| cam9 | rtmpose_m | 10.6 | 184 |
| cam10 | rtmpose_m | 11.6 | 62 |
| cam11 | rtmpose_m | 10.6 | 150 |
| cam12 | rtmpose_m | 12.6 | 108 |
| **合计** | — | **136.0 fps** | 平均约 **111 ms** |

> 配置目标 `frame_rate=15` / 路，12 路理论上限 **180 fps**；实测合计 **136 fps（≈75%）**，单路约 **10～12 fps**。4090 在 **80% GPU 利用率**下已接近当前模型组合（10× rtmpose_m + 2× rtmpose_t）上限。

### 8.5 结论

| 项 | 结论 |
|----|------|
| 12 路并行推理 | ✅ **通过**（持续运行，无容器退出） |
| event-worker 消费 | ✅ `lag=0`，无 Redis 积压 |
| 瓶颈 | **153 GPU 算力**（80% util）；非 Redis / MediaMTX |
| WSL 推流 | 12/12 ready，未成为本次瓶颈 |
| 优化方向 | 全路改 `rtmpose_t`、增大 `pose_frame_interval`、或多卡分流 |

### 8.6 汇总表

| 指标 | 目标 | 实测 |
|------|------|------|
| MediaMTX ready | 12/12 | **12/12** |
| infer running | 12 | **12** |
| GPU 利用率 | 记录峰值 | **80%** |
| GPU 显存 | 记录峰值 | **13244 MiB** |
| Redis lag | 0 | **0** |
| 单路 infer fps | ~15 | **10.2～12.6** |
| 12 路合计 pose 吞吐 | — | **136 fps** |

### 8.7 与第一轮对比

| 轮次 | 时间 | ready | infer running | GPU | 结果 |
|------|------|-------|---------------|-----|------|
| 第一轮 | 19:09 | 0/12 | 0 | 0% | mediamtx restart 导致推流断开，infer 404 退出 |
| 第二轮 | 19:14 | 12/12 | 12 | 80% | **有效负载测试完成** |
