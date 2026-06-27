/** 回放时间轴：优先落库 video_time_sec（读帧同线程媒体时间），与浏览器 currentTime 对齐。 */
export function resolveFramePlaybackSec(frame, fallbackFps = 25) {
  const raw = frame?.video_time_sec;
  if (raw != null && !Number.isNaN(Number(raw))) {
    return Math.max(0, Number(raw));
  }
  const fps = Number(frame?.video_fps ?? fallbackFps) || 25;
  const idx = Number(frame?.frame_idx) || 1;
  return Math.max(0, (idx - 1) / fps);
}

export function findFrameAtPlaybackTime(frames, t, fallbackFps = 25) {
  if (!frames?.length) return null;
  let lo = 0;
  let hi = frames.length - 1;
  let best = null;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const cur = frames[mid];
    const sec = resolveFramePlaybackSec(cur, fallbackFps);
    if (sec <= t) {
      best = cur;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return best;
}
