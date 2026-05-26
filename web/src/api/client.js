const fetchOpts = { credentials: 'include' };

async function parseJson(resp) {
  const data = await resp.json();
  if (resp.status === 401 && !String(window.location.pathname).startsWith('/login')) {
    window.location.assign(`/login?from=${encodeURIComponent(window.location.pathname)}`);
    throw new Error('未登录或会话已过期');
  }
  return data;
}

export async function apiGet(path) {
  const resp = await fetch(path, fetchOpts);
  return parseJson(resp);
}

export async function apiPost(path, body) {
  const resp = await fetch(path, {
    ...fetchOpts,
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return parseJson(resp);
}

export async function apiDelete(path, body) {
  const resp = await fetch(path, {
    ...fetchOpts,
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  return parseJson(resp);
}

export async function apiPut(path, body) {
  const resp = await fetch(path, {
    ...fetchOpts,
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return parseJson(resp);
}

export async function apiPatch(path, body) {
  const resp = await fetch(path, {
    ...fetchOpts,
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return parseJson(resp);
}

/** 仅读取 POST /capture 已落盘的 jpg，不会触发 RTSP 抓帧 */
export function thumbnailUrl(cameraId, lastFrameAt) {
  const t = lastFrameAt ? Math.floor(lastFrameAt) : 0;
  return `/api/cameras/${encodeURIComponent(cameraId)}/thumbnail?t=${t}`;
}

export function cameraPlaybackUrl(cameraId) {
  return `/api/cameras/${encodeURIComponent(cameraId)}/playback`;
}

/** SSE：推理 overlay（骨架/碰撞），与姿态帧同频推送 */
export function openCameraLiveStream(cameraId, { onFrame, onReady, onError } = {}) {
  const url = `/api/cameras/${encodeURIComponent(cameraId)}/live/stream`;
  const es = new EventSource(url);
  const handleFrame = (ev) => {
    try {
      const data = JSON.parse(ev.data);
      onFrame?.(data);
    } catch (e) {
      onError?.(e);
    }
  };
  es.addEventListener('frame', handleFrame);
  es.addEventListener('ready', (ev) => {
    try {
      onReady?.(JSON.parse(ev.data));
    } catch {
      onReady?.(null);
    }
  });
  es.onerror = () => {
    onError?.(new Error('实时 overlay 连接中断'));
  };
  return () => {
    es.removeEventListener('frame', handleFrame);
    es.close();
  };
}

export function formatDuration(seconds) {
  const s = Math.max(0, parseInt(seconds, 10) || 0);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return [h, m, sec].map((n) => String(n).padStart(2, '0')).join(':');
}
