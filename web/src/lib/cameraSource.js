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
