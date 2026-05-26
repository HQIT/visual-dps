export const STREAM_FORMATS = ['webrtc', 'hls'];
export const DEFAULT_STREAM_PREFS = { format: 'webrtc' };

function storageKey(cameraId) {
  return `monitorStreamPrefs:${cameraId}`;
}

function normalizeFormat(format) {
  if (format === 'mjpeg') return 'webrtc';
  return STREAM_FORMATS.includes(format) ? format : DEFAULT_STREAM_PREFS.format;
}

export function loadStreamPrefs(cameraId) {
  if (!cameraId) return { ...DEFAULT_STREAM_PREFS };
  try {
    const raw = localStorage.getItem(storageKey(cameraId));
    if (!raw) return { ...DEFAULT_STREAM_PREFS };
    const parsed = JSON.parse(raw);
    return { format: normalizeFormat(parsed.format) };
  } catch {
    return { ...DEFAULT_STREAM_PREFS };
  }
}

export function saveStreamPrefs(cameraId, prefs) {
  if (!cameraId || !prefs) return;
  localStorage.setItem(
    storageKey(cameraId),
    JSON.stringify({ format: normalizeFormat(prefs.format) }),
  );
}
