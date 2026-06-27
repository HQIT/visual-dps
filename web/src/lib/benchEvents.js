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
      events.push({
        kind: 'alarm',
        token,
        boxLabel: formatCollisionBoxLabel(token),
      });
    }
    for (const token of hits) {
      if (alarmSet.has(token)) continue;
      events.push({
        kind: 'collision',
        token,
        boxLabel: formatCollisionBoxLabel(token),
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

export function filterFrameEventGroups(groups, filter) {
  if (filter === 'all') return groups;
  return groups
    .map((group) => ({
      ...group,
      events: group.events.filter((ev) => ev.kind === filter),
    }))
    .filter((group) => group.events.length > 0);
}
