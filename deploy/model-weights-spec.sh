# 推理权重清单与最小字节（存在且 size >= min 才视为完整）
# 被 check-model-weights.sh、download-*-weights.sh 共用

vdps_each_required_model_weight() {
  cat <<'EOF'
rtmpose_onnx/rtmdet_nano/end2end.onnx
rtmpose_onnx/rtmdet_m/end2end.onnx
rtmpose_onnx/rtmpose_t/end2end.onnx
rtmpose_onnx/rtmpose_s/end2end.onnx
rtmpose_onnx/rtmpose_m/end2end.onnx
yolo_pose/yolo26n-pose.pt
yolo_pose/yolo26s-pose.pt
yolo_pose/yolo26m-pose.pt
yolo_pose/yolo26l-pose.pt
EOF
}

vdps_model_min_bytes() {
  case "$1" in
    rtmpose_onnx/rtmdet_nano/end2end.onnx) echo 500000 ;;
    rtmpose_onnx/rtmdet_m/end2end.onnx) echo 10000000 ;;
    rtmpose_onnx/rtmpose_t/end2end.onnx) echo 8000000 ;;
    rtmpose_onnx/rtmpose_s/end2end.onnx) echo 15000000 ;;
    rtmpose_onnx/rtmpose_m/end2end.onnx) echo 25000000 ;;
    yolo_pose/yolo26n-pose.pt) echo 5000000 ;;
    yolo_pose/yolo26s-pose.pt) echo 15000000 ;;
    yolo_pose/yolo26m-pose.pt) echo 35000000 ;;
    yolo_pose/yolo26l-pose.pt) echo 45000000 ;;
    *) echo 100000 ;;
  esac
}

# RTMPose zip 缓存最小体积（subdir 名，如 rtmpose_t）
vdps_rtmpose_zip_min_bytes() {
  case "$1" in
    rtmdet_nano) echo 2000000 ;;
    rtmdet_m) echo 50000000 ;;
    rtmpose_t) echo 5000000 ;;
    rtmpose_s) echo 10000000 ;;
    rtmpose_m) echo 20000000 ;;
    *) echo 100000 ;;
  esac
}

# $1=models 目录  $2=相对路径；完整返回 0
vdps_model_weight_file_ok() {
  models_dir="$1"
  rel="$2"
  path="${models_dir%/}/${rel}"
  need="$(vdps_model_min_bytes "${rel}")"
  if [ ! -f "${path}" ]; then
    return 1
  fi
  size="$(wc -c < "${path}" | tr -d ' ')"
  [ "${size}" -ge "${need}" ]
}
