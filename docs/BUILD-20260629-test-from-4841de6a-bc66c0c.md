# 镜像构建记录：20260629-test-from-4841de6a-bc66c0c

> 分支 `test-from-4841de6a`，当前 HEAD **`bc66c0c`**（含碰撞告警 UI 配置、FieldHint 生效说明等）。  
> 构建日期：2026-06-29

---

## 目标 tag 与 `.env`

统一 tag：

```
20260629-test-from-4841de6a-bc66c0c
```

项目根 `.env`：

```env
VISUAL_DPS_IMAGE_TAG=20260629-test-from-4841de6a-bc66c0c
INFERENCE_LITE_IMAGE=visual-dps-inference-lite:20260629-test-from-4841de6a-bc66c0c
INFERENCE_LITE_GPU_IMAGE=visual-dps-inference-lite-gpu:20260629-test-from-4841de6a-bc66c0c
INFERENCE_LITE_GPU_ONNX_IMAGE=visual-dps-inference-lite-gpu-onnx:20260629-test-from-4841de6a-bc66c0c
```

**无需修改** `docker-compose.yml` / `docker-compose.deploy.yml`（均使用 `${VISUAL_DPS_IMAGE_TAG}`）。

---

## 一条命令全量构建

```bash
cd ~/workspace/visual-dps && export DOCKER_TAG_ALSO_LATEST=1 && ./scripts/build-inference-lite-gpu-image.sh && ./scripts/build-inference-lite-gpu-onnx-image.sh && ./scripts/verify-gpu-onnx-image.sh visual-dps-inference-lite-gpu-onnx:20260629-test-from-4841de6a-bc66c0c && ./scripts/build-ui-image.sh
```

带启动：

```bash
cd ~/workspace/visual-dps && export DOCKER_TAG_ALSO_LATEST=1 && ./scripts/build-inference-lite-gpu-image.sh && ./scripts/build-inference-lite-gpu-onnx-image.sh && ./scripts/verify-gpu-onnx-image.sh visual-dps-inference-lite-gpu-onnx:20260629-test-from-4841de6a-bc66c0c && ./scripts/build-ui-image.sh --up
```

> tag 由 `.env` 中 `VISUAL_DPS_IMAGE_TAG` 决定；构建脚本会先 `source .env`，**勿仅 export 而不改 .env**。

---

## 产出镜像

| 镜像 | Tag |
|------|-----|
| `visual-dps-visual-dps-ui` | `20260629-test-from-4841de6a-bc66c0c` |
| `visual-dps-event-worker` | `20260629-test-from-4841de6a-bc66c0c` |
| `visual-dps-inference-lite-gpu` | `20260629-test-from-4841de6a-bc66c0c` |
| `visual-dps-inference-lite-gpu-onnx` | `20260629-test-from-4841de6a-bc66c0c` |
| `visual-dps-inference-lite` | `20260629-test-from-4841de6a-bc66c0c`（若构建） |

---

## 验证

```bash
docker images | grep 20260629-test-from-4841de6a-bc66c0c
curl -s http://127.0.0.1:${UI_PORT:-8046}/api/version
```

---

## 部署包同步

若使用 `visual-dps-0628-deploy/app`，需同步该目录下 `app/.env` 中上述四行，且 `HOST_PROJECT_ROOT` 指向部署目录绝对路径。

离线包：`./scripts/export-offline-one-shot.sh`（构建完成后再导出）。
