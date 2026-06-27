import { parseCollisionToken } from './annotation';
import { resolveFramePlaybackSec } from './benchTime';

/** 碰撞 token → 展示用货框标签 */
export function formatCollisionBoxLabel(token) {
  const { shelf_code, box_id } = parseCollisionToken(token);
  if (shelf_code && box_id) return `${shelf_code}:${box_id}`;
  return box_id || String(token || '');
}

/**
 * 从 pose 帧列表提取按帧分组的事件（碰撞 + 告警）。
 * 同一帧多货框时，每个货框单独一条事件；告警帧上的货框只记告警，不重复记碰撞。
 */
export function buildFrameEventGroups(frames, fallbackFps = 25) {
  if (!Array.isArray(frames)) return [];

  const groups = [];
  for (const frame of frames) {
    const hits = Array.isArray(frame.collisions) ? frame.collisions : [];
    const alarms = Array.isArray(frame.alarm_collisions) ? frame.alarm_collisions : [];
    if (!hits.length && !alarms.length) continue;

    const alarmSet = new Set(alarms);
    const events = [];

    for (const token of alarms) {
      const { shelf_code, box_id } = parseCollisionToken(token);
      events.push({
        kind: 'alarm',
        token,
        boxLabel: formatCollisionBoxLabel(token),
        boxId: box_id,
        shelfCode: shelf_code,
      });
    }
    for (const token of hits) {
      if (alarmSet.has(token)) continue;
      const { shelf_code, box_id } = parseCollisionToken(token);
      events.push({
        kind: 'collision',
        token,
        boxLabel: formatCollisionBoxLabel(token),
        boxId: box_id,
        shelfCode: shelf_code,
      });
    }

    if (!events.length) continue;

    const playbackSec = resolveFramePlaybackSec(frame, fallbackFps);
    groups.push({
      key: `${frame.frame_idx}-${playbackSec}`,
      frame_idx: frame.frame_idx,
      video_time_sec: playbackSec,
      events,
    });
  }
  return groups;
}

export function countFrameEvents(groups) {
  let collisions = 0;
  let alarms = 0;
  for (const group of groups || []) {
    for (const ev of group.events || []) {
      if (ev.kind === 'alarm') alarms += 1;
      else collisions += 1;
    }
  }
  return { collisions, alarms, frames: (groups || []).length };
}

/** 事件是否匹配货框筛选（支持 token / box_id / 展示标签，含部分匹配） */
export function eventMatchesBoxFilter(ev, boxFilter) {
  const q = String(boxFilter || '').trim().toLowerCase();
  if (!q) return true;
  const token = String(ev?.token || '').toLowerCase();
  const boxId = String(ev?.boxId || '').toLowerCase();
  const boxLabel = String(ev?.boxLabel || '').toLowerCase();
  if (token === q || boxId === q || boxLabel === q) return true;
  return boxId.includes(q) || boxLabel.includes(q) || token.includes(q);
}

/** 从事件组提取去重后的货框选项（供下拉/联想输入） */
export function collectEventBoxOptions(groups) {
  const map = new Map();
  for (const group of groups || []) {
    for (const ev of group.events || []) {
      const key = ev.token || ev.boxId || ev.boxLabel;
      if (!key || map.has(key)) continue;
      map.set(key, {
        value: key,
        label: ev.boxLabel || ev.boxId || key,
        boxId: ev.boxId || '',
      });
    }
  }
  return [...map.values()].sort((a, b) => (
    String(a.label).localeCompare(String(b.label), 'zh-CN', { numeric: true })
  ));
}

export function filterFrameEventGroups(groups, filter, boxFilter = '') {
  let result = groups;
  if (filter !== 'all') {
    result = result
      .map((group) => ({
        ...group,
        events: group.events.filter((ev) => ev.kind === filter),
      }))
      .filter((group) => group.events.length > 0);
  }
  const boxQ = String(boxFilter || '').trim();
  if (!boxQ) return result;
  return result
    .map((group) => ({
      ...group,
      events: group.events.filter((ev) => eventMatchesBoxFilter(ev, boxQ)),
    }))
    .filter((group) => group.events.length > 0);
}
