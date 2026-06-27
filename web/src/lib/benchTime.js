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
  const time = Math.max(0, Number(t) || 0);
  // 右边界二分：取 start <= t 的最后一帧（frames 按 video_time_sec 升序）
  let lo = 0;
  let hi = frames.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    const sec = resolveFramePlaybackSec(frames[mid], fallbackFps);
    if (sec <= time + 1e-9) lo = mid;
    else hi = mid - 1;
  }
  const start = resolveFramePlaybackSec(frames[lo], fallbackFps);
  if (time + 1e-9 < start) return null;
  return frames[lo];
}

export function findFrameByIdx(frames, frameIdx) {
  if (!frames?.length || frameIdx == null) return null;
  const target = Number(frameIdx);
  return frames.find((f) => Number(f.frame_idx) === target) ?? null;
}

/** 点击侧栏帧事件时 seek：落在该帧时间窗中点，减轻 HTML5 keyframe 回退一帧 */
export function seekSecForFrame(frame, fallbackFps = 25) {
  const start = resolveFramePlaybackSec(frame, fallbackFps);
  const fps = Number(frame?.video_fps ?? fallbackFps) || 25;
  return start + 0.5 / fps;
}
