#!/usr/bin/env bash
# UI 启停推理容器时 bind mount 的 app/ 下文件（与 inference_container_service 一致）
INFER_BIND_MOUNT_FILES=(
  inference_worker.py
  core/config.py
  core/ort_runtime.py
  services/inference_service.py
  services/hwaccel_probe.py
  services/nvidia_pip_cuda.py
  services/rtsp_capture.py
  services/wall_clock.py
  services/pipeline_log.py
  services/pose_bus.py
  services/event_engine/sharding.py
  services/runtime_config_service.py
  services/inference_backends/__init__.py
  services/inference_backends/model_registry.py
  services/inference_backends/rtmpose_onnx_backend.py
  services/inference_backends/onnx_assets.py
  services/inference_backends/yolo_pose_backend.py
)

check_infer_bind_mounts() {
  local app_dir="$1"
  local fail=0
  local rel
  for rel in "${INFER_BIND_MOUNT_FILES[@]}"; do
    local path="${app_dir}/${rel}"
    if [[ -f "${path}" ]]; then
      echo "OK: app/${rel}"
    else
      echo "FAIL: 缺少 app/${rel}（或为目录，请 rm -rf 后从仓库复制）" >&2
      fail=1
    fi
  done
  return "${fail}"
}
