/** 与系统全局配置对齐、可按摄像头覆盖的项 */

export const INFERENCE_BACKEND_OPTIONS = [
  {
    value: 'mmpose',
    label: 'MMPose GPU（RTMDet-M + RTMPose-M，需 inference-lite-gpu 镜像）',
    shortLabel: 'MMPose GPU',
  },
  {
    value: 'rtmpose_onnx',
    label: 'RTMPose-T ONNX（轻量多路，CPU/GPU ONNX）',
    shortLabel: 'RTMPose-T',
  },
];

export const CAMERA_OVERRIDE_FIELDS = [
  {
    key: 'models.backend',
    label: '推理模型',
    type: 'select',
    options: INFERENCE_BACKEND_OPTIONS,
    hint: '修改后需重新启动该路智能检测。MMPose 需 GPU 推理镜像；RTMPose-T 可用 lite 镜像。',
  },
  { key: 'inference.frame_rate', label: '推理帧率 (fps)', type: 'number', min: 1, max: 60 },
  { key: 'inference.height', label: '推理高度 (px)', type: 'number', min: 120, max: 2160 },
  { key: 'inference.pose_frame_interval', label: '姿态检测间隔 (帧)', type: 'number', min: 1, max: 120 },
  { key: 'debug-info.enabled', label: '预览可视化', type: 'boolean' },
];

export function formatSettingDisplayValue(field, value) {
  if (value === undefined || value === null || value === '') return '—';
  if (field.type === 'boolean') return value ? '开' : '关';
  if (field.type === 'select' && field.options) {
    const opt = field.options.find((o) => o.value === value);
    return opt?.shortLabel || opt?.label || String(value);
  }
  return String(value);
}

export function backendLabel(value) {
  return formatSettingDisplayValue(
    { type: 'select', options: INFERENCE_BACKEND_OPTIONS },
    value,
  );
}

/** 监控页展示：优先用推理容器实际 backend，其次摄像头 effective_settings */
export function resolveCameraModelLabel(camera) {
  if (!camera) return '—';
  const backend =
    camera.inference?.backend || camera.effective_settings?.['models.backend'];
  return backendLabel(backend);
}
