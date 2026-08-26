/** 边是否属于该摄像头（避免 ui/mtx 共享端点误匹配其他 cam 的 preview 边） */
export function edgeBelongsToCamera(edge, cameraId) {
  if (!cameraId) return true;
  const id = String(edge?.id || '');
  if (id.startsWith('e:redis->event-worker')) return true;
  const metaCid = edge?.meta?.camera_id;
  if (metaCid != null && metaCid !== '') {
    return String(metaCid) === cameraId;
  }
  if (id.startsWith(`e:${cameraId}:`)) return true;
  if (id.endsWith(`:${cameraId}`)) return true;
  return false;
}

export function edgesIncidentToNode(edges, nodeId) {
  if (!nodeId) return edges;
  return edges.filter((e) => e.from === nodeId || e.to === nodeId);
}

export function cameraIdFromNode(node) {
  const cid = node?.meta?.camera_id;
  if (cid) return String(cid);
  const m = /^[^:]+:(.+)$/.exec(String(node?.id || ''));
  if (m && (node?.kind === 'source' || node?.kind === 'inference')) {
    return m[1];
  }
  return '';
}
