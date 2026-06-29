/** 与系统全局配置对齐、可按摄像头覆盖的项 */

export const RTMPOSE_BACKEND_OPTIONS = [
  { value: 'rtmpose_t', label: 'RTMPose-T（ONNX）', shortLabel: 'RTMPose-T' },
  { value: 'rtmpose_s', label: 'RTMPose-S（ONNX）', shortLabel: 'RTMPose-S' },
  { value: 'rtmpose_m', label: 'RTMPose-M（ONNX）', shortLabel: 'RTMPose-M' },
];

export const YOLO_BACKEND_OPTIONS = [
  { value: 'yolo26n_pose', label: 'YOLO26n-pose（端到端）', shortLabel: 'YOLO26n' },
  { value: 'yolo26s_pose', label: 'YOLO26s-pose（端到端）', shortLabel: 'YOLO26s' },
  { value: 'yolo26m_pose', label: 'YOLO26m-pose（端到端）', shortLabel: 'YOLO26m' },
  { value: 'yolo26l_pose', label: 'YOLO26l-pose（端到端）', shortLabel: 'YOLO26l' },
];

export const RTMDET_OPTIONS = [
  { value: 'nano', label: 'RTMDet-nano（320×320）', shortLabel: 'RTMDet-nano' },
  { value: 'm', label: 'RTMDet-M（640×640）', shortLabel: 'RTMDet-M' },
];

/** 单下拉兼容列表（监控等） */
export const INFERENCE_MODEL_OPTIONS = [...RTMPOSE_BACKEND_OPTIONS, ...YOLO_BACKEND_OPTIONS];

/** @deprecated 使用 INFERENCE_MODEL_OPTIONS */
export const INFERENCE_BACKEND_OPTIONS = INFERENCE_MODEL_OPTIONS;

export const DEFAULT_RTM_DET = 'nano';

/** 不参与通用字段循环渲染的推理模型键 */
export const INFERENCE_MODEL_SETTING_KEYS = ['models.backend', 'models.det'];

export const CAMERA_OVERRIDE_FIELDS = [
  {
    key: 'inference.frame_rate',
    label: '推理帧率 (fps)',
    type: 'number',
    min: 1,
    max: 60,
  },
  { key: 'inference.height', label: '推理高度 (px)', type: 'number', min: 120, max: 2160 },
  { key: 'inference.pose_frame_interval', label: '姿态检测间隔 (帧)', type: 'number', min: 1, max: 120 },
  {
    key: 'debug-info.enabled',
    label: '推理调试日志',
    type: 'boolean',
    hint: '开启后推理容器周期性输出 [DEBUG-INFO]（帧率、资源等）。不影响监控页画面与骨架叠加，生产环境建议关闭。',
  },
];

/** 旧配置 / 族 id → 当前 preset id（与 model_registry._ALIASES 对齐） */
const BACKEND_ALIASES = {
  lite: 'rtmpose_t',
  mp: 'rtmpose_t',
  mediapipe: 'rtmpose_t',
  mmpose: 'rtmpose_t',
  mm: 'rtmpose_t',
  default: 'rtmpose_t',
  rtmpose_onnx: 'rtmpose_t',
  'rtmpose-t': 'rtmpose_t',
  yolo_pose: 'yolo26s_pose',
};

export function normalizeBackendId(value) {
  const v = String(value || '').trim().toLowerCase();
  return BACKEND_ALIASES[v] || v;
}

export function isRtmposeBackend(value) {
  return normalizeBackendId(value).startsWith('rtmpose_');
}

export function normalizeDetId(value) {
  const v = String(value || '').trim().toLowerCase();
  return v === 'm' ? 'm' : DEFAULT_RTM_DET;
}

export function backendShortLabel(value) {
  const normalized = normalizeBackendId(value);
  const opt =
    RTMPOSE_BACKEND_OPTIONS.find((o) => o.value === normalized) ||
    YOLO_BACKEND_OPTIONS.find((o) => o.value === normalized);
  return opt?.shortLabel || opt?.label || String(value || '—');
}

export function detShortLabel(value) {
  const normalized = normalizeDetId(value);
  return RTMDET_OPTIONS.find((o) => o.value === normalized)?.shortLabel || DEFAULT_RTM_DET;
}

export function formatSettingDisplayValue(field, value) {
  if (value === undefined || value === null || value === '') return '—';
  if (field.type === 'boolean') return value ? '开' : '关';
  if (field.type === 'select' && field.options) {
    const normalized = normalizeBackendId(value);
    const opt = field.options.find((o) => o.value === normalized);
    return opt?.shortLabel || opt?.label || String(value);
  }
  return String(value);
}

export function backendLabel(value) {
  return backendShortLabel(value);
}

/** 监控页展示：优先用推理容器实际 backend，其次摄像头 effective_settings */
export function resolveCameraModelLabel(camera) {
  if (!camera) return '—';
  const backend = normalizeBackendId(
    camera.inference?.backend || camera.effective_settings?.['models.backend'],
  );
  if (isRtmposeBackend(backend)) {
    const det = normalizeDetId(camera.effective_settings?.['models.det']);
    return `${backendShortLabel(backend)} + ${detShortLabel(det)}`;
  }
  return backendShortLabel(backend);
}

/** 合并 effective / global / 表单 override，得到当前生效的 backend 与 det */
export function resolveEffectiveInferenceModel({
  settings = {},
  effectiveSettings = {},
  globalDefaults = {},
}) {
  const backendCustom = Object.prototype.hasOwnProperty.call(settings, 'models.backend');
  const detCustom = Object.prototype.hasOwnProperty.call(settings, 'models.det');
  const backend = normalizeBackendId(
    backendCustom
      ? settings['models.backend']
      : effectiveSettings['models.backend'] ?? globalDefaults['models.backend'] ?? 'rtmpose_t',
  );
  const det = normalizeDetId(
    detCustom
      ? settings['models.det']
      : effectiveSettings['models.det'] ?? globalDefaults['models.det'] ?? DEFAULT_RTM_DET,
  );
  return { backend, det, backendCustom, detCustom };
}
