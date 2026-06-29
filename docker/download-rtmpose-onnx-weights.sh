#!/usr/bin/env sh
# 下载 RTMDet-nano + RTMPose t/s/m ONNX（已有且完整则跳过；OPENMMLAB_MIRROR_BASE 加速）
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
ROOT="$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=deploy/model-weights-spec.sh
. "${ROOT}/deploy/model-weights-spec.sh"
# shellcheck source=docker/download-http.sh
. "${SCRIPT_DIR}/download-http.sh"

DEST="${1:-localdata/models/rtmpose_onnx}"
SDK_BASE="$(vdps_openmmlab_base)/mmpose/v1/projects/rtmposev1/onnx_sdk"

fetch_one() {
  sub="$1"
  zip_name="$2"
  rel="rtmpose_onnx/${sub}/end2end.onnx"
  out_dir="${DEST}/${sub}"
  out_onnx="${out_dir}/end2end.onnx"
  zip_cache="${out_dir}/_source.zip"
  need_onnx="$(vdps_model_min_bytes "${rel}")"
  need_zip="$(vdps_rtmpose_zip_min_bytes "${sub}")"
  url="${SDK_BASE}/${zip_name}"

  mkdir -p "${out_dir}"

  if [ -f "${out_onnx}" ]; then
    osize="$(wc -c < "${out_onnx}" | tr -d ' ')"
    if [ "${osize}" -ge "${need_onnx}" ]; then
      echo "==> 已有且完整 ${out_onnx} (${osize} >= ${need_onnx} bytes)"
      return 0
    fi
    echo "==> ONNX 不完整，将重新准备 ${sub} (${osize} < ${need_onnx})"
    rm -f "${out_onnx}"
  fi

  if [ -f "${zip_cache}" ]; then
    zsize="$(wc -c < "${zip_cache}" | tr -d ' ')"
    if [ "${zsize}" -lt "${need_zip}" ]; then
      echo "==> zip 缓存不完整，将重新下载 ${zip_name}"
      rm -f "${zip_cache}" "${zip_cache}.part"
    fi
  fi

  if [ ! -f "${zip_cache}" ]; then
    vdps_fetch_file "${url}" "${zip_cache}" "${need_zip}" "${zip_name}" || return 1
  else
    echo "==> 使用完整 zip 缓存 ${zip_cache}（跳过重复下载）"
  fi

  if ! command -v unzip >/dev/null 2>&1; then
    echo "错误: 需要 unzip" >&2
    return 1
  fi
  unzip -q -o -j "${zip_cache}" "*/end2end.onnx" -d "${out_dir}"

  if [ ! -f "${out_onnx}" ]; then
    echo "错误: 解压后缺少 ${out_onnx}" >&2
    return 1
  fi
  osize="$(wc -c < "${out_onnx}" | tr -d ' ')"
  if [ "${osize}" -lt "${need_onnx}" ]; then
    echo "错误: 解压后 ONNX 不完整 ${out_onnx} (${osize} < ${need_onnx})" >&2
    rm -f "${out_onnx}" "${zip_cache}"
    return 1
  fi
  echo "    -> ${out_onnx} (${osize} bytes, 完整)"
}

fetch_one rtmdet_nano "rtmdet_nano_8xb32-100e_coco-obj365-person-05d8511e.zip"
fetch_one rtmdet_m "rtmdet_m_8xb32-100e_coco-obj365-person-235e8209.zip"
fetch_one rtmpose_t "rtmpose-t_simcc-body7_pt-body7_420e-256x192-026a1439_20230504.zip"
fetch_one rtmpose_s "rtmpose-s_simcc-body7_pt-body7_420e-256x192-acd4a1ef_20230504.zip"
fetch_one rtmpose_m "rtmpose-m_simcc-body7_pt-body7_420e-256x192-e48f03d0_20230504.zip"

echo "==> RTMPose ONNX 已就绪: ${DEST}"
