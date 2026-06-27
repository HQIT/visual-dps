const STORAGE_KEY = 'visual-dps.benchCreateParams';

function clampPoseInterval(value) {
  return Math.max(1, Math.min(120, Number(value) || 3));
}

function clampAlarmMin(value) {
  return Math.max(1, Math.min(120, Number(value) || 3));
}

function clampAlarmCooldown(value) {
  return Math.max(0, Math.min(600, Number(value) || 0));
}

/** 持久化「新建评测」区参数，供列表/回放页重跑时使用 */
export function saveBenchCreateParams({
  backend,
  poseFrameInterval,
  alarmMinConsecutiveFrames,
  alarmCooldownFrames,
}) {
  try {
    sessionStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        backend: String(backend || ''),
        poseFrameInterval: clampPoseInterval(poseFrameInterval),
        alarmMinConsecutiveFrames: clampAlarmMin(alarmMinConsecutiveFrames),
        alarmCooldownFrames: clampAlarmCooldown(alarmCooldownFrames),
      }),
    );
  } catch {
    /* 忽略 storage 不可用 */
  }
}

export function loadBenchCreateParams(fallback = {}) {
  const base = {
    backend: fallback.backend || 'rtmpose_t',
    poseFrameInterval: clampPoseInterval(fallback.poseFrameInterval ?? 3),
    alarmMinConsecutiveFrames: clampAlarmMin(fallback.alarmMinConsecutiveFrames ?? 3),
    alarmCooldownFrames: clampAlarmCooldown(fallback.alarmCooldownFrames ?? 0),
  };
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return base;
    const data = JSON.parse(raw);
    return {
      backend: String(data.backend || base.backend),
      poseFrameInterval: clampPoseInterval(data.poseFrameInterval ?? base.poseFrameInterval),
      alarmMinConsecutiveFrames: clampAlarmMin(
        data.alarmMinConsecutiveFrames ?? base.alarmMinConsecutiveFrames,
      ),
      alarmCooldownFrames: clampAlarmCooldown(
        data.alarmCooldownFrames ?? base.alarmCooldownFrames,
      ),
    };
  } catch {
    return base;
  }
}

export function benchRerunPayload({
  backend,
  poseFrameInterval,
  alarmMinConsecutiveFrames,
  alarmCooldownFrames,
}) {
  return {
    backend: String(backend || ''),
    pose_frame_interval: clampPoseInterval(poseFrameInterval),
    alarm_min_consecutive_frames: clampAlarmMin(alarmMinConsecutiveFrames),
    alarm_cooldown_frames: clampAlarmCooldown(alarmCooldownFrames),
  };
}

export function benchCreateFormFields({
  backend,
  poseFrameInterval,
  alarmMinConsecutiveFrames,
  alarmCooldownFrames,
}) {
  return {
    backend: String(backend || ''),
    pose_frame_interval: String(clampPoseInterval(poseFrameInterval)),
    alarm_min_consecutive_frames: String(clampAlarmMin(alarmMinConsecutiveFrames)),
    alarm_cooldown_frames: String(clampAlarmCooldown(alarmCooldownFrames)),
  };
}
