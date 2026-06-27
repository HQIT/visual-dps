/** 与系统全局配置对齐、可按摄像头覆盖的项 */

export const INFERENCE_MODEL_OPTIONS = [
  { value: 'rtmpose_t', label: 'RTMPose-T + RTMDet-nano（ONNX）', shortLabel: 'RTMPose-T' },
  { value: 'rtmpose_s', label: 'RTMPose-S + RTMDet-nano（ONNX）', shortLabel: 'RTMPose-S' },
  { value: 'rtmpose_m', label: 'RTMPose-M + RTMDet-nano（ONNX）', shortLabel: 'RTMPose-M' },
  { value: 'yolo26n_pose', label: 'YOLO26n-pose（端到端）', shortLabel: 'YOLO26n' },
  { value: 'yolo26s_pose', label: 'YOLO26s-pose（端到端）', shortLabel: 'YOLO26s' },
  { value: 'yolo26m_pose', label: 'YOLO26m-pose（端到端）', shortLabel: 'YOLO26m' },
  { value: 'yolo26l_pose', label: 'YOLO26l-pose（端到端）', shortLabel: 'YOLO26l' },
];

/** @deprecated 使用 INFERENCE_MODEL_OPTIONS */
export const INFERENCE_BACKEND_OPTIONS = INFERENCE_MODEL_OPTIONS;

export const CAMERA_OVERRIDE_FIELDS = [
  {
    key: 'models.backend',
    label: '推理模型',
    type: 'select',
    options: INFERENCE_MODEL_OPTIONS,
    hint: '修改后需重新启动该路智能检测。RTMPose 需 lite / lite-gpu-onnx 镜像；YOLO 需含 ultralytics 的 GPU 镜像。',
  },
  { key: 'inference.frame_rate', label: '推理帧率 (fps)', type: 'number', min: 1, max: 60 },
  { key: 'inference.height', label: '推理高度 (px)', type: 'number', min: 120, max: 2160 },
  { key: 'inference.pose_frame_interval', label: '姿态检测间隔 (帧)', type: 'number', min: 1, max: 120 },
  {
    key: 'inference.alarm_min_consecutive_frames',
    label: '告警连续碰撞帧数',
    type: 'number',
    min: 1,
    max: 120,
    hint: '同一货框连续 N 帧碰撞后才触发告警；调小可更快告警，调大可减少误报。',
  },
  {
    key: 'inference.alarm_cooldown_frames',
    label: '告警冷却帧数',
    type: 'number',
    min: 0,
    max: 600,
    hint: '同一货框两次告警之间的最小帧间隔；0 表示无冷却（每段连续碰撞均可告警）。修改后重跑评测或重启 event worker 生效。',
  },
  {
    key: 'debug-info.enabled',
    label: '推理调试日志',
    type: 'boolean',
    hint: '开启后推理容器周期性输出 [DEBUG-INFO]（帧率、资源等）。不影响监控页画面与骨架叠加，生产环境建议关闭。',
  },
];

const LEGACY_BACKEND_LABELS = {
  rtmpose_onnx: 'RTMPose-T（旧 ID）',
};

export function formatSettingDisplayValue(field, value) {
  if (value === undefined || value === null || value === '') return '—';
  if (field.type === 'boolean') return value ? '开' : '关';
  if (field.type === 'select' && field.options) {
    const opt = field.options.find((o) => o.value === value);
    return opt?.shortLabel || opt?.label || LEGACY_BACKEND_LABELS[value] || String(value);
  }
  return String(value);
}

export function backendLabel(value) {
  return formatSettingDisplayValue(
    { type: 'select', options: INFERENCE_MODEL_OPTIONS },
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
