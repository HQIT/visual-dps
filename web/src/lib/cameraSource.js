/** 与 services/mediamtx_service SOURCE_* 一致 */
export const CAMERA_SOURCE_OPTIONS = [
  { value: 'external', label: '外部（本机 MediaMTX 播放地址）' },
  { value: 'rtsp_pull', label: '主动拉流（上游 RTSP）' },
];

export function streamUrlFromCamera(cam) {
  if (!cam) return '';
  if (cam.source_type === 'rtsp_pull' && cam.pull_url) {
    return cam.pull_url;
  }
  return cam.url || '';
}

/** 编辑表单中的自定义本机播放地址（与默认相同时留空） */
export function playbackUrlFieldFromCamera(cam, defaultPlaybackUrl) {
  if (!cam) return '';
  const u = String(cam.url || '').trim();
  const def = String(defaultPlaybackUrl || cam.default_playback_url || '').trim();
  if (!u) return '';
  if (def && u === def) return '';
  if (cam.source_type === 'rtsp_pull') return u;
  if (cam.playback_url_custom) return u;
  return '';
}

/** 与后端 _validate_stream_url 规则一致的前端校验 */
export function validateStreamUrl(url) {
  const s = String(url || '').trim();
  if (!s) return null;
  let parsed;
  try {
    parsed = new URL(s);
  } catch {
    return '视频流地址格式无效，请使用 rtsp://、rtsps://、http:// 或 https:// 开头';
  }
  const scheme = (parsed.protocol || '').replace(/:$/, '').toLowerCase();
  if (!['rtsp', 'rtsps', 'http', 'https'].includes(scheme)) {
    return '视频流地址需以 rtsp://、rtsps://、http:// 或 https:// 开头';
  }
  if (!parsed.hostname) {
    return '视频流地址无效：缺少主机名';
  }
  if (/^\d{1,3}(\.\d{1,3}){3}$/.test(parsed.hostname)) {
    const octets = parsed.hostname.split('.').map((x) => Number(x));
    if (octets.length !== 4 || octets.some((o) => o < 0 || o > 255)) {
      return 'IP 地址无效（每段应为 0–255）';
    }
  }
  return null;
}
