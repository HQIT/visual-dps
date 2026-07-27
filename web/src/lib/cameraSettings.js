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

/** 推理模型字段说明（问号 tooltip） */
export const INFERENCE_MODEL_FIELD_HINTS = {
  backend: {
    hint: 'RTMPose 为 top-down（先检人再估姿态）；YOLO26-pose 为 bottom-up 端到端。',
    effectHint: '保存后需对该路摄像头重新「启动智能检测」（重建 visual-dps-infer-{摄像头ID} 推理容器）。',
  },
  det: {
    hint: '仅 RTMPose 流程使用；nano 更快，M 精度更高。',
    effectHint: '保存后需对该路摄像头重新「启动智能检测」（重建 visual-dps-infer-{摄像头ID} 推理容器）。',
  },
};

export const CAMERA_OVERRIDE_FIELDS = [
  {
    key: 'inference.frame_rate',
    label: '推理帧率 (fps)',
    type: 'number',
    min: 1,
    max: 60,
    hint: '推理主循环目标帧率；实际 fps 受模型耗时限制。',
    effectHint: '保存后需重新「启动智能检测」（visual-dps-infer-{摄像头ID} 推理容器）。',
  },
  {
    key: 'inference.height',
    label: '推理高度 (px)',
    type: 'number',
    min: 120,
    max: 2160,
    hint: '推理与标注缩放基准高度，影响精度与负载。',
    effectHint: '保存后需重新「启动智能检测」（visual-dps-infer-{摄像头ID} 推理容器）。',
  },
  {
    key: 'inference.pose_frame_interval',
    label: '姿态检测间隔 (帧)',
    type: 'number',
    min: 1,
    max: 120,
    hint: '每隔 N 帧做一次姿态推理；越大负载越低、碰撞采样越稀疏。',
    effectHint: '保存后需重新「启动智能检测」（visual-dps-infer-{摄像头ID} 推理容器）。',
  },
  {
    key: 'debug-info.enabled',
    label: '推理调试日志',
    type: 'boolean',
    hint: '开启后推理容器周期性输出 [DEBUG-INFO]（帧率、资源等）。不影响监控页画面与骨架叠加，生产环境建议关闭。',
    effectHint: '保存后需重新「启动智能检测」（visual-dps-infer-{摄像头ID} 推理容器）。',
  },
  {
    key: 'pipeline_log.enabled',
    label: '流水线阶段日志',
    type: 'boolean',
    hint: '记录该路采帧、推理发布及 Worker 消费阶段（[PIPELINE]）。显式开启时优先于全局默认（全局默认关）；关闭可减轻 worker.log 积压。',
    effectHint:
      '保存后需重新「启动智能检测」；Worker 侧会随 camera_ips.json 热更新，infer 侧需重启容器。',
  },
];

/** 仅全局设置页：流水线阶段日志（infer / event-worker 读取） */
const PIPELINE_LOG_EFFECT_HINT =
  'event-worker 保存后自动生效（开关/采样/stdout）；infer 容器同步热更新采样与开关。变更日志目录或轮转参数需重启 infer 容器与 event-worker。';

/** 与 services/runtime_config_service._default_pipeline_log_section 对齐 */
export const PIPELINE_LOG_SYSTEM_DEFAULTS = {
  'pipeline_log.enabled': false,
  'pipeline_log.file_enabled': false,
  'pipeline_log.stdout': true,
  'pipeline_log.dir': 'localdata/logs/pipeline',
  'pipeline_log.sample': 30,
  'pipeline_log.max_bytes': 52_428_800,
  'pipeline_log.backup_count': 5,
};

export const GLOBAL_PIPELINE_LOG_FIELDS = [
  {
    key: 'pipeline_log.enabled',
    label: '流水线阶段日志（未自定义摄像头的默认值）',
    type: 'boolean',
    default: PIPELINE_LOG_SYSTEM_DEFAULTS['pipeline_log.enabled'],
    hint: '未在摄像头页单独配置的路是否默认记录 [PIPELINE]；默认关。某路在摄像头页显式开启时不受此项限制。',
    effectHint: PIPELINE_LOG_EFFECT_HINT,
  },
  {
    key: 'pipeline_log.file_enabled',
    label: '写入日志文件',
    type: 'boolean',
    default: PIPELINE_LOG_SYSTEM_DEFAULTS['pipeline_log.file_enabled'],
    hint: '开启后将日志写入 pipeline 日志目录下按角色命名的 .log 文件（支持轮转）。',
    effectHint: PIPELINE_LOG_EFFECT_HINT,
  },
  {
    key: 'pipeline_log.stdout',
    label: '输出到 stdout',
    type: 'boolean',
    default: PIPELINE_LOG_SYSTEM_DEFAULTS['pipeline_log.stdout'],
    hint: '开启后 docker logs 可见 [PIPELINE] 行；关闭且仅写文件时需在挂载目录 tail。',
    effectHint: PIPELINE_LOG_EFFECT_HINT,
  },
  {
    key: 'pipeline_log.dir',
    label: '日志目录',
    type: 'text',
    default: PIPELINE_LOG_SYSTEM_DEFAULTS['pipeline_log.dir'],
    hint: '相对项目根或容器 /app 的路径。',
    effectHint: PIPELINE_LOG_EFFECT_HINT,
  },
  {
    key: 'pipeline_log.sample',
    label: '日志采样间隔 (帧)',
    type: 'number',
    min: 1,
    max: 600,
    default: PIPELINE_LOG_SYSTEM_DEFAULTS['pipeline_log.sample'],
    hint: '每 N 帧输出一条阶段日志；告警回调 enqueue 不受采样限制。',
    effectHint: PIPELINE_LOG_EFFECT_HINT,
  },
  {
    key: 'pipeline_log.max_bytes',
    label: '单文件大小上限 (字节)',
    type: 'number',
    min: 1024,
    max: 1073741824,
    default: PIPELINE_LOG_SYSTEM_DEFAULTS['pipeline_log.max_bytes'],
    defaultLabel: '52428800（50MB）',
    hint: 'RotatingFileHandler 单文件上限，超出后轮转。',
    effectHint: PIPELINE_LOG_EFFECT_HINT,
  },
  {
    key: 'pipeline_log.backup_count',
    label: '日志备份份数',
    type: 'number',
    min: 0,
    max: 30,
    default: PIPELINE_LOG_SYSTEM_DEFAULTS['pipeline_log.backup_count'],
    hint: '轮转保留的历史文件数，0 表示仅覆盖当前文件。',
    effectHint: PIPELINE_LOG_EFFECT_HINT,
  },
];

/** 仅全局设置页：碰撞告警门控（event-worker 读取） */
export const GLOBAL_COLLISION_FIELDS = [
  {
    key: 'inference.alarm_min_consecutive_frames',
    label: '告警连续命中帧数',
    type: 'number',
    min: 1,
    max: 120,
    hint: '同一货位手腕连续命中多少帧才从碰撞（黄）升为告警（红）并触发回调。',
    effectHint:
      '保存后 visual-dps-event-worker 会自动读取 localdata/runtime_config.json；若未生效请执行 docker restart visual-dps-event-worker。',
  },
  {
    key: 'inference.alarm_cooldown_frames',
    label: '告警冷却帧数',
    type: 'number',
    min: 0,
    max: 600,
    hint: '同一货位两次告警之间的最小帧间隔；0 表示不冷却。',
    effectHint:
      '保存后 visual-dps-event-worker 会自动读取 localdata/runtime_config.json；若未生效请执行 docker restart visual-dps-event-worker。',
  },
];

/** 仅全局设置页：碰撞前置门控（event-worker 读取） */
export const GLOBAL_PREFILTER_FIELDS = [
  {
    key: 'collision_prefilter.enabled',
    label: '启用碰撞前置门控',
    type: 'boolean',
    hint: '开启后：ankle_max_speed_norm@0.081770 + triple90 + shknee140；关闭时与现网 baseline 相同。',
    effectHint:
      '保存后 visual-dps-event-worker 会自动读取 localdata/runtime_config.json；若未生效请执行 docker restart visual-dps-event-worker。',
  },
  {
    key: 'collision_prefilter.speed_threshold',
    label: '踝部归一化速度阈值',
    type: 'number',
    min: 0.01,
    max: 0.5,
    step: 0.000001,
    hint: 'ankle_max_speed_norm 超过此值且未满足 triple90 豁免、且判定为站立时，跳过手腕碰撞检测。标定默认 0.081770。',
    effectHint:
      '保存后 visual-dps-event-worker 会自动读取 localdata/runtime_config.json；若未生效请执行 docker restart visual-dps-event-worker。',
  },
  {
    key: 'collision_prefilter.arm_torso_min',
    label: 'triple90：肩-躯干角下限 (°)',
    type: 'number',
    min: 0,
    max: 180,
    hint: 'arm_torso_angle_max 豁免条件之一。',
    effectHint:
      '保存后 visual-dps-event-worker 会自动读取 localdata/runtime_config.json；若未生效请执行 docker restart visual-dps-event-worker。',
  },
  {
    key: 'collision_prefilter.elbow_min',
    label: 'triple90：肘角均值下限 (°)',
    type: 'number',
    min: 0,
    max: 180,
    hint: 'elbow_angle_mean 豁免条件之一。',
    effectHint:
      '保存后 visual-dps-event-worker 会自动读取 localdata/runtime_config.json；若未生效请执行 docker restart visual-dps-event-worker。',
  },
  {
    key: 'collision_prefilter.wrist_elevation_min',
    label: 'triple90：腕抬升角下限 (°)',
    type: 'number',
    min: 0,
    max: 180,
    hint: 'wrist_elevation_angle_max 豁免条件之一。',
    effectHint:
      '保存后 visual-dps-event-worker 会自动读取 localdata/runtime_config.json；若未生效请执行 docker restart visual-dps-event-worker。',
  },
  {
    key: 'collision_prefilter.stance_threshold',
    label: '站立判定：肩-髋-膝角下限 (°)',
    type: 'number',
    min: 0,
    max: 180,
    hint: 'shoulder_hip_knee_angle_min 低于此值视为蹲姿，不 block；缺角度时视为站立。',
    effectHint:
      '保存后 visual-dps-event-worker 会自动读取 localdata/runtime_config.json；若未生效请执行 docker restart visual-dps-event-worker。',
  },
  {
    key: 'collision_prefilter.max_pose_gap_sec',
    label: '姿态断流重置间隔 (秒)',
    type: 'number',
    min: 0,
    max: 30,
    step: 0.01,
    hint: '相邻 pose 墙钟间隔超过此值则重置速度历史；0 表示自动（interval/frame_rate×2.5）。',
    effectHint:
      '保存后 visual-dps-event-worker 会自动读取 localdata/runtime_config.json；若未生效请执行 docker restart visual-dps-event-worker。',
  },
];

/** 合并 hint 与生效说明，供 FieldHint 展示 */
export function settingsFieldTooltip(field) {
  if (!field) return '';
  const lines = [];
  if (field.hint) lines.push(field.hint);
  if (field.effectHint) lines.push(`生效：${field.effectHint}`);
  return lines.join('\n\n');
}

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

/** 设置页展示字段的系统默认值（优先 defaultLabel） */
export function formatFieldDefaultValue(field) {
  if (!field) return '—';
  if (field.defaultLabel) return field.defaultLabel;
  const fallback = field.default ?? PIPELINE_LOG_SYSTEM_DEFAULTS[field.key];
  return formatSettingDisplayValue(field, fallback);
}

/** 读取设置项当前值，缺省时回落到字段 default */
export function resolveSettingValue(settings, field) {
  const raw = settings?.[field.key];
  if (raw !== undefined && raw !== null && raw !== '') return raw;
  if (field.default !== undefined) return field.default;
  return field.type === 'boolean' ? false : '';
}

export function backendLabel(value) {
  return backendShortLabel(value);
}

/** 监控页展示：优先用推理容器实际 backend + rtm_det，其次 effective_settings */
export function resolveCameraModelLabel(camera) {
  if (!camera) return '—';
  const backend = normalizeBackendId(
    camera.inference?.backend || camera.effective_settings?.['models.backend'],
  );
  if (isRtmposeBackend(backend)) {
    const det = normalizeDetId(
      camera.inference?.rtm_det || camera.effective_settings?.['models.det'],
    );
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
