/** 推理来源与边缘能力（与 source_type 正交） */

export const INFERENCE_MODES = [
  { value: 'central', label: '中心检测（默认）', hint: '在本机启动推理容器，从视频流做姿态检测。' },
  {
    value: 'edge',
    label: '边缘节点',
    hint: '不在中心启动推理；由边缘通过 REST 上报 pose（需配置 Keycloak Client）。',
  },
  { value: 'disabled', label: '不检测', hint: '不启动中心推理，也不期望边缘 pose。' },
];

export const EDGE_CAPABILITIES = [
  { value: 'pose', label: 'Pose 流' },
  { value: 'video', label: '视频流（需配置 RTSP / MediaMTX）' },
  {
    value: 'collision',
    label: '边缘碰撞（Rule-based）',
    hint: '碰撞在边缘计算，通过 events API 上报；中心 event-worker 不再对该路做碰撞。',
  },
];

export function normalizeInferenceMode(value) {
  const mode = String(value || 'central').trim().toLowerCase();
  return INFERENCE_MODES.some((m) => m.value === mode) ? mode : 'central';
}

export function isEdgeCamera(cam) {
  return normalizeInferenceMode(cam?.inference_mode) === 'edge';
}

export function edgeCapabilitiesFromCam(cam) {
  const raw = cam?.edge_capabilities;
  if (Array.isArray(raw)) return raw.map((x) => String(x).trim()).filter(Boolean);
  return [];
}

export function inferenceModeLabel(value) {
  return INFERENCE_MODES.find((m) => m.value === value)?.label || value || '—';
}

/** 边缘路：在线/检测文案（与 RTSP online 分离） */
export function edgeOnlineLabel(cam) {
  if (!isEdgeCamera(cam)) return null;
  const er = cam?.edge_runtime;
  if (!er) return '待上报';
  if (er.agent_alive) return '边缘在线';
  if (er.last_status_age_sec != null) return '边缘失联';
  return '待上报';
}

export function edgeInferDisplay(cam) {
  if (!isEdgeCamera(cam)) return null;
  const er = cam?.edge_runtime;
  if (er?.display) return er.display;
  return '边缘上报';
}
