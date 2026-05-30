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
    key: 'debug-info.enabled',
    label: '推理调试日志',
    type: 'boolean',
    hint: '开启后推理容器周期性输出 [DEBUG-INFO]（帧率、资源等）。不影响监控页画面与骨架叠加，生产环境建议关闭。',
  },
];

/** Event Worker 碰撞检测全局参数（设置 → 碰撞检测 Tab） */
export const COLLISION_SETTING_KEYS = [
  'inference.collision.min_consecutive_frames',
  'inference.collision.cooldown_frames',
  'inference.collision.window_frames',
  'inference.collision.wrist_conf',
  'inference.collision.elbow_conf',
  'inference.collision.forearm_extend_ratio',
  'inference.collision.boundary_margin_ratio',
  'inference.collision.boundary_margin_min_px',
  'inference.collision.track_max_match_dist',
  'inference.collision.track_stale_sec',
  'inference.collision.per_track_gating',
];

export const COLLISION_SETTINGS_FIELDS = [
  {
    key: 'inference.collision.min_consecutive_frames',
    label: '告警最少命中帧 (M)',
    type: 'number',
    min: 1,
    max: 30,
    hint: '滑动窗口内至少命中 M 帧才触发告警。',
  },
  {
    key: 'inference.collision.window_frames',
    label: '告警滑动窗口 (N)',
    type: 'number',
    min: 1,
    max: 60,
    hint: 'M-of-N 门控的窗口大小，需 ≥ 最少命中帧。',
  },
  {
    key: 'inference.collision.cooldown_frames',
    label: '告警冷却 (帧)',
    type: 'number',
    min: 1,
    max: 600,
    hint: '同一货位两次告警之间的最小帧间隔。',
  },
  {
    key: 'inference.collision.wrist_conf',
    label: '手腕置信度阈值',
    type: 'number',
    min: 0.1,
    max: 1,
    step: 0.05,
    hint: '低于此值的腕点不参与碰撞判定。',
  },
  {
    key: 'inference.collision.elbow_conf',
    label: '肘部置信度阈值',
    type: 'number',
    min: 0.1,
    max: 1,
    step: 0.05,
    hint: '低于此值时不做前臂外推。',
  },
  {
    key: 'inference.collision.forearm_extend_ratio',
    label: '前臂外推比例',
    type: 'number',
    min: 0,
    max: 1,
    step: 0.05,
    hint: '0 表示仅用手腕点；越大越容易跨相邻货位，建议 0.15~0.25。',
  },
  {
    key: 'inference.collision.boundary_margin_ratio',
    label: '软边界比例 (×肩宽)',
    type: 'number',
    min: 0,
    max: 0.5,
    step: 0.01,
    hint: '手部点距 ROI 边界在此范围内仍算命中，用于抗抖。',
  },
  {
    key: 'inference.collision.boundary_margin_min_px',
    label: '软边界最小 (px)',
    type: 'number',
    min: 0,
    max: 50,
    step: 1,
    hint: '软边界像素下限，密集货架建议 2~4。',
  },
  {
    key: 'inference.collision.track_max_match_dist',
    label: '人体跟踪最大距离 (px)',
    type: 'number',
    min: 50,
    max: 500,
    step: 10,
    hint: '帧间 anchor 匹配的最大像素距离。',
  },
  {
    key: 'inference.collision.track_stale_sec',
    label: '跟踪过期 (秒)',
    type: 'number',
    min: 0.3,
    max: 10,
    step: 0.1,
    hint: '超过此时间未出现的 track 将被清理。',
  },
  {
    key: 'inference.collision.per_track_gating',
    label: '按人独立门控',
    type: 'boolean',
    hint: '开启后多人先后取同一货位各自计数；关闭则按货位全局门控。',
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
