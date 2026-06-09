/** 摄像头流类型与表单字段（与后端 source_type 对齐） */

import { normalizeInferenceMode } from './cameraModes';

export const DEFAULT_SOURCE_TYPE = 'rtsp_pull';

export const CAMERA_SOURCE_TYPES = [
  {
    value: 'rtsp_pull',
    label: '拉取外部流',
    hint: '本机 MediaMTX 从外部 RTSP 地址拉流；请填写「上游地址」与「本机播放地址」。',
  },
  {
    value: 'publisher',
    label: '外部推流',
    hint: '外部设备或脚本向本机 MediaMTX 的通道推流；下方为本机播放地址（可自动生成）。',
  },
  {
    value: 'external',
    label: '直连 RTSP',
    hint: '填写完整 RTSP/RTSPS 地址，不经 MediaMTX 配置拉流或推流；推理与预览直接使用该地址。',
  },
];

/** 已废弃类型（仅展示旧数据） */
const LEGACY_SOURCE_LABELS = {
  v4l2: '本地摄像头（已废弃）',
};

export const DEFAULT_RTSP_HOST = '127.0.0.1';
export const DEFAULT_RTSP_PORT = '8554';

const LOCAL_MTX_HOSTS = new Set(['127.0.0.1', 'localhost', 'mediamtx']);

export function defaultPlaybackUrl(path, host = DEFAULT_RTSP_HOST, port = DEFAULT_RTSP_PORT) {
  const slug = String(path || '').trim().replace(/^\/+/, '');
  if (!slug) return '';
  return `rtsp://${host}:${port}/${slug}`;
}

function parseRtspPath(url) {
  try {
    const u = new URL(url);
    const parts = u.pathname.split('/').filter(Boolean);
    return parts[parts.length - 1] || '';
  } catch {
    return '';
  }
}

/** 播放地址是否指向本机 MediaMTX 的该通道 */
export function isLocalMediamtxPlaybackUrl(url, path) {
  const slug = String(path || '').trim();
  if (!slug || !url) return false;
  try {
    const u = new URL(url);
    if (!['rtsp:', 'rtsps:'].includes(u.protocol)) return false;
    const host = (u.hostname || '').toLowerCase();
    if (!LOCAL_MTX_HOSTS.has(host)) return false;
    return parseRtspPath(url) === slug;
  } catch {
    return false;
  }
}

/** 加载表单时规范化流类型（v4l2 迁移为外部推流） */
export function normalizeSourceTypeForForm(cam) {
  const path = cam?.path || cam?.id || '';
  const raw = cam?.source_type || DEFAULT_SOURCE_TYPE;
  let source_type = raw;
  let url = cam?.url || '';
  let pull_url = cam?.pull_url || '';

  if (raw === 'v4l2') {
    source_type = 'publisher';
    if (!url || !isLocalMediamtxPlaybackUrl(url, path)) {
      url = defaultPlaybackUrl(path) || url;
    }
    pull_url = '';
  }

  return { source_type, url, pull_url };
}

export function emptyCameraForm() {
  return {
    path: '',
    name: '',
    source_type: DEFAULT_SOURCE_TYPE,
    url: '',
    pull_url: '',
    enabled: true,
    inference_mode: 'central',
    edge_capabilities: ['pose'],
    settings: {},
  };
}

/** 从 API 摄像头记录填充抽屉表单（勿把 pull_url 填入 url） */
export function cameraToForm(cam) {
  if (!cam) return emptyCameraForm();
  const { source_type, url, pull_url } = normalizeSourceTypeForForm(cam);
  return {
    path: cam.path || cam.id || '',
    name: cam.name || '',
    source_type,
    url,
    pull_url,
    enabled: cam.enabled !== false,
    inference_mode: normalizeInferenceMode(cam.inference_mode),
    edge_capabilities: Array.isArray(cam.edge_capabilities) && cam.edge_capabilities.length
      ? [...cam.edge_capabilities]
      : ['pose'],
    settings: { ...(cam.settings || {}) },
  };
}

/** 保存前组装 payload */
export function formToCameraPayload(form) {
  const path = String(form.path || '').trim();
  const name = String(form.name || '').trim();
  const source_type = form.source_type || DEFAULT_SOURCE_TYPE;
  const payload = {
    path,
    name,
    source_type,
    enabled: form.enabled !== false,
    inference_mode: normalizeInferenceMode(form.inference_mode),
    settings: form.settings || {},
  };

  if (payload.inference_mode === 'edge') {
    payload.edge_capabilities = (form.edge_capabilities || ['pose']).filter(Boolean);
  }

  const needsVideo =
    payload.inference_mode !== 'edge'
    || (payload.edge_capabilities || []).includes('video');

  if (source_type === 'rtsp_pull') {
    payload.pull_url = String(form.pull_url || '').trim();
    payload.url = String(form.url || '').trim() || defaultPlaybackUrl(path);
  } else if (source_type === 'external') {
    payload.url = needsVideo ? String(form.url || '').trim() : String(form.url || '').trim();
    payload.pull_url = '';
  } else {
    payload.url = String(form.url || '').trim() || (needsVideo ? defaultPlaybackUrl(path) : '');
    payload.pull_url = '';
  }
  return payload;
}

export function sourceTypeLabel(value) {
  return (
    CAMERA_SOURCE_TYPES.find((t) => t.value === value)?.label
    || LEGACY_SOURCE_LABELS[value]
    || value
    || '—'
  );
}
