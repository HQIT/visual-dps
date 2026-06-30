# Visual-DPS 0629 纯部署包（ce4fe0a）

镜像 tag：`20260629-test-from-4841de6a-ce4fe0a`

## 内容

| 路径 | 说明 |
|------|------|
| `app/` | compose、`.env`、localdata、推理 bind mount 源码 |
| `weights/` | 推理权重 + SHA256SUMS |
| `install.sh` / `verify-images.sh` / `verify-package.sh` | 安装与校验 |

**不含 Docker 镜像**（现网须已 `docker load`）。

## 153 安装步骤

```bash
cd ~/workspace/visual-dps-0629-deploy

# 1) 确认镜像（GPU 可跳过 CPU lite）
./verify-images.sh --skip-lite-cpu

# 2) 包内容
./verify-package.sh

# 3) 确认 app/.env
#    HOST_PROJECT_ROOT=/home/hqit/workspace/visual-dps-0629-deploy/app
#    MEDIAMTX_PUBLIC_HOST=192.168.1.153
#    UI_PORT=8046
#    VISUAL_DPS_IMAGE_TAG=20260629-test-from-4841de6a-ce4fe0a

# 4) 安装
./install.sh --host 192.168.1.153 --stop-infer
```

访问：`http://192.168.1.153:8046/`

## 全局推理配置（runtime_config.json）

- backend: `rtmpose_m` + det `m`
- `pose_frame_interval`: 2
- 告警门控: `alarm_min_consecutive_frames=3`, `alarm_cooldown_frames=0`

各摄像头 `camera_ips.json` 无单独 `settings`，统一走全局配置。改配置后需在 UI **停启智能检测**。

## 缺 CPU lite 镜像时

```bash
docker tag visual-dps-inference-lite:20260628-test-from-4841de6a-56f47b1 \
  visual-dps-inference-lite:20260629-test-from-4841de6a-ce4fe0a
```

## 验证

```bash
curl -s http://127.0.0.1:8046/api/version | python3 -m json.tool
docker ps | grep visual-dps
```
