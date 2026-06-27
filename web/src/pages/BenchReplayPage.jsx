import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import MonitorPreviewStage from '../components/MonitorPreviewStage';
import { apiGet, apiPost } from '../api/client';
import { parseAnnotationPayload } from '../lib/annotation';
import {
  buildFrameEventGroups,
  collectEventBoxOptions,
  countFrameEvents,
  filterFrameEventGroups,
} from '../lib/benchEvents';
import { formatUserError } from '../lib/userFacingText';
import { benchRerunPayload, loadBenchCreateParams } from '../lib/benchCreateParams';
import { findFrameAtPlaybackTime, findFrameByIdx, resolveFramePlaybackSec, seekSecForFrame } from '../lib/benchTime';
import './BenchPage.css';

function formatTime(sec) {
  if (sec == null || Number.isNaN(sec)) return '00:00.000';
  const total = Math.max(0, Number(sec));
  const m = Math.floor(total / 60);
  const s = Math.floor(total % 60);
  const ms = Math.floor((total - Math.floor(total)) * 1000);
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}.${String(ms).padStart(3, '0')}`;
}

export default function BenchReplayPage() {
  const { runId } = useParams();
  const videoRef = useRef(null);
  const pinSeekRef = useRef(false);
  const [videoEl, setVideoEl] = useState(null);
  const [run, setRun] = useState(null);
  const [frames, setFrames] = useState([]);
  const [annotation, setAnnotation] = useState({
    boxes: [],
    shelves: [],
    shelfCorners: [],
    annotationSize: null,
    gridShape: [],
  });
  const [currentTime, setCurrentTime] = useState(0);
  const [error, setError] = useState('');
  const [showSkeletonLayer, setShowSkeletonLayer] = useState(true);
  const [showRoiLayer, setShowRoiLayer] = useState(true);
  const [eventFilter, setEventFilter] = useState('all');
  const [boxFilter, setBoxFilter] = useState('');
  const [rerunLoading, setRerunLoading] = useState(false);
  /** 侧栏点击帧事件后锁定骨架，直到用户播放或拖动进度条 */
  const [pinnedFrameIdx, setPinnedFrameIdx] = useState(null);

  const handleVideoElement = useCallback((el) => {
    videoRef.current = el;
    setVideoEl(el);
  }, []);

  const loadAll = useCallback(async () => {
    if (!runId) return;
    setError('');
    try {
      const [runRes, frameRes] = await Promise.all([
        apiGet(`/api/benchmark/runs/${encodeURIComponent(runId)}`),
        apiGet(`/api/benchmark/runs/${encodeURIComponent(runId)}/frames`),
      ]);
      if (runRes.status !== 'success') {
        setError(formatUserError(runRes.error) || '加载失败');
        return;
      }
      setRun(runRes.run);
      setFrames(frameRes.items || []);
      const cid = runRes.run?.camera_id;
      if (cid) {
        const ann = await apiGet(`/api/cameras/${encodeURIComponent(cid)}/annotation`);
        if (ann.status === 'success') {
          setAnnotation(parseAnnotationPayload(ann));
        }
      }
    } catch (err) {
      setError(formatUserError(err.message) || '加载失败');
    }
  }, [runId]);

  useEffect(() => {
    loadAll();
    const timer = setInterval(() => {
      if (run?.status === 'running') loadAll();
    }, 3000);
    return () => clearInterval(timer);
  }, [loadAll, run?.status]);

  // 播放时用 rAF 跟视频时间轴同步骨架/货框
  useEffect(() => {
    const video = videoEl;
    if (!video) return undefined;

    let rafId = 0;
    let active = true;

    const tick = () => {
      if (!active) return;
      if (!video.paused && !video.ended) {
        setCurrentTime(video.currentTime);
      }
      rafId = requestAnimationFrame(tick);
    };

    const onPlay = () => {
      setPinnedFrameIdx(null);
      cancelAnimationFrame(rafId);
      rafId = requestAnimationFrame(tick);
    };
    const onPause = () => {
      cancelAnimationFrame(rafId);
      setCurrentTime(video.currentTime);
    };
    const onSeeked = () => {
      setCurrentTime(video.currentTime);
      if (pinSeekRef.current) {
        pinSeekRef.current = false;
        return;
      }
      setPinnedFrameIdx(null);
    };

    video.addEventListener('play', onPlay);
    video.addEventListener('pause', onPause);
    video.addEventListener('seeked', onSeeked);
    if (!video.paused && !video.ended) onPlay();

    return () => {
      active = false;
      cancelAnimationFrame(rafId);
      video.removeEventListener('play', onPlay);
      video.removeEventListener('pause', onPause);
      video.removeEventListener('seeked', onSeeked);
    };
  }, [videoEl]);

  const runFps = Number(run?.video_fps) || 25;

  const eventGroups = useMemo(
    () => buildFrameEventGroups(frames, runFps),
    [frames, runFps],
  );
  const eventStats = useMemo(() => countFrameEvents(eventGroups), [eventGroups]);
  const boxOptions = useMemo(() => collectEventBoxOptions(eventGroups), [eventGroups]);
  const filteredEventGroups = useMemo(
    () => filterFrameEventGroups(eventGroups, eventFilter, boxFilter),
    [eventGroups, eventFilter, boxFilter],
  );
  const filteredEventStats = useMemo(
    () => countFrameEvents(filteredEventGroups),
    [filteredEventGroups],
  );

  const activeFrame = useMemo(() => {
    if (pinnedFrameIdx != null) {
      const pinned = findFrameByIdx(frames, pinnedFrameIdx);
      if (pinned) return pinned;
    }
    return findFrameAtPlaybackTime(frames, currentTime, runFps);
  }, [frames, currentTime, runFps, pinnedFrameIdx]);
  const activePlaybackSec = activeFrame
    ? resolveFramePlaybackSec(activeFrame, runFps)
    : null;
  const liveSkeletons = useMemo(
    () => (activeFrame?.persons ? activeFrame.persons : []),
    [activeFrame],
  );
  const liveHits = useMemo(
    () => (Array.isArray(activeFrame?.collisions) ? activeFrame.collisions : []),
    [activeFrame],
  );
  const liveAlarms = useMemo(
    () => (Array.isArray(activeFrame?.alarm_collisions) ? activeFrame.alarm_collisions : []),
    [activeFrame],
  );

  const seekToFrameIdx = useCallback((frameIdx) => {
    const frame = findFrameByIdx(frames, frameIdx);
    if (!frame) return;
    const video = videoRef.current;
    const seekSec = seekSecForFrame(frame, runFps);
    setPinnedFrameIdx(Number(frameIdx));
    pinSeekRef.current = true;
    if (video) {
      video.pause();
      const max = Number.isFinite(video.duration) && video.duration > 0 ? video.duration : seekSec;
      video.currentTime = Math.min(Math.max(0, seekSec), max);
      setCurrentTime(video.currentTime);
    } else {
      setCurrentTime(seekSec);
    }
  }, [frames, runFps]);

  const seekTo = (sec) => {
    setPinnedFrameIdx(null);
    const video = videoRef.current;
    if (!video) return;
    video.currentTime = Math.max(0, Number(sec) || 0);
    setCurrentTime(video.currentTime);
  };

  const handleRerun = async () => {
    if (!runId || !run) return;
    const params = loadBenchCreateParams({
      backend: run.backend,
      poseFrameInterval: run.config?.['inference.pose_frame_interval'],
      alarmMinConsecutiveFrames: run.config?.['inference.alarm_min_consecutive_frames'],
      alarmCooldownFrames: run.config?.['inference.alarm_cooldown_frames'],
    });
    const detail = [
      `模型：${params.backend || '—'}`,
      `姿态间隔：${params.poseFrameInterval}`,
      `告警连续帧：${params.alarmMinConsecutiveFrames}`,
      `告警冷却帧：${params.alarmCooldownFrames}`,
      '（取自评测页「新建评测」区当前参数）',
    ].filter(Boolean).join('\n');
    if (!window.confirm(`按新建评测区参数重跑？\n\n${detail}\n\n将清空原有结果。`)) return;
    setRerunLoading(true);
    setError('');
    try {
      const res = await apiPost(
        `/api/benchmark/runs/${encodeURIComponent(runId)}/rerun`,
        benchRerunPayload(params),
      );
      if (res.status !== 'success') {
        setError(formatUserError(res.error) || res.message || '重跑失败');
        return;
      }
      setFrames([]);
      await loadAll();
    } catch (err) {
      setError(formatUserError(err.message) || '重跑失败');
    } finally {
      setRerunLoading(false);
    }
  };

  const videoUrl = runId ? `/api/benchmark/runs/${encodeURIComponent(runId)}/video` : '';
  const exportUrl = runId ? `/api/benchmark/runs/${encodeURIComponent(runId)}/export` : '';
  const poseDriftSec = activePlaybackSec != null
    ? Math.abs(currentTime - activePlaybackSec)
    : null;

  return (
    <div className="bench-page bench-replay-page">
      <header className="bench-header">
        <div>
          <Link to="/bench" className="bench-back">← 返回列表</Link>
          <h1>{run?.title || runId || '回放'}</h1>
          {run ? (
            <p>
              {run.camera_id} · {run.backend}
              {run.config?.['inference.pose_frame_interval'] != null
                ? ` · 间隔 ${run.config['inference.pose_frame_interval']}`
                : ''}
              {' · '}{run.status} · pose {run.pose_frame_count ?? 0}
              {' · '}碰撞 {eventStats.collisions} · 告警 {eventStats.alarms}
            </p>
          ) : null}
        </div>
        <div className="bench-replay-toggles">
          {run && run.status !== 'running' ? (
            <a href={exportUrl} className="bench-export-btn" download>
              导出 Excel
            </a>
          ) : null}
          {run && run.status !== 'running' ? (
            <button type="button" className="bench-rerun-btn" disabled={rerunLoading} onClick={handleRerun}>
              {rerunLoading ? '重跑中…' : '重跑'}
            </button>
          ) : null}
          <label><input type="checkbox" checked={showRoiLayer} onChange={(e) => setShowRoiLayer(e.target.checked)} /> 货框</label>
          <label><input type="checkbox" checked={showSkeletonLayer} onChange={(e) => setShowSkeletonLayer(e.target.checked)} /> 骨架</label>
        </div>
      </header>

      {error ? <div className="bench-error">{error}</div> : null}

      <div className="bench-replay-layout">
        <div className="bench-replay-main">
          <div className="bench-replay-stage">
            <MonitorPreviewStage
              compactPreview
              filePreviewUrl={videoUrl}
              filePreviewPlayback
              onVideoElement={handleVideoElement}
              boxes={annotation.boxes}
              shelves={annotation.shelves}
              gridShape={annotation.gridShape}
              shelfCorners={annotation.shelfCorners}
              annotationSize={annotation.annotationSize}
              inferRunning
              hits={liveHits}
              alarms={liveAlarms}
              liveSkeletons={liveSkeletons}
              liveInferWidth={activeFrame?.infer_width || run?.video_width || 0}
              liveInferHeight={activeFrame?.infer_height || run?.video_height || 0}
              showSkeletonLayer={showSkeletonLayer}
              showRoiLayer={showRoiLayer}
              emptyText={run ? '视频加载中…' : '加载任务…'}
            />
          </div>
          <div className="bench-replay-time">
            当前 {formatTime(currentTime)}
            {activeFrame ? ` · 帧 ${activeFrame.frame_idx} · pose @ ${formatTime(activePlaybackSec)}` : ''}
            {poseDriftSec != null && poseDriftSec > 0.08 ? (
              <span className="bench-replay-drift"> · 偏差 {Math.round(poseDriftSec * 1000)}ms</span>
            ) : null}
          </div>
        </div>

        <aside className="bench-replay-side">
          <div className="bench-replay-side-head">
            <h3>帧事件</h3>
            <p className="bench-replay-side-stats">
              {boxFilter || eventFilter !== 'all'
                ? `${filteredEventStats.frames} / ${eventStats.frames} 帧 · 碰撞 ${filteredEventStats.collisions} · 告警 ${filteredEventStats.alarms}`
                : `${eventStats.frames} 帧 · 碰撞 ${eventStats.collisions} · 告警 ${eventStats.alarms}`}
            </p>
          </div>
          <div className="bench-event-box-filter">
            <label htmlFor="bench-box-filter">货框 ID</label>
            <div className="bench-event-box-filter-row">
              <input
                id="bench-box-filter"
                type="search"
                className="bench-event-box-search"
                placeholder="输入或选择 box_id"
                value={boxFilter}
                onChange={(e) => setBoxFilter(e.target.value)}
                list="bench-box-id-list"
              />
              <select
                className="bench-event-box-select"
                value={boxOptions.some((opt) => opt.value === boxFilter) ? boxFilter : ''}
                onChange={(e) => setBoxFilter(e.target.value)}
                aria-label="选择货框"
              >
                <option value="">全部</option>
                {boxOptions.map((opt) => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </select>
            </div>
            <datalist id="bench-box-id-list">
              {boxOptions.map((opt) => (
                <option key={opt.value} value={opt.boxId || opt.label} />
              ))}
            </datalist>
          </div>
          <div className="bench-event-filters" role="tablist" aria-label="事件筛选">
            {[
              { id: 'all', label: '全部' },
              { id: 'collision', label: '碰撞' },
              { id: 'alarm', label: '告警' },
            ].map((item) => (
              <button
                key={item.id}
                type="button"
                role="tab"
                aria-selected={eventFilter === item.id}
                className={`bench-event-filter${eventFilter === item.id ? ' active' : ''}`}
                onClick={() => setEventFilter(item.id)}
              >
                {item.label}
              </button>
            ))}
          </div>
          {!filteredEventGroups.length ? (
            <p className="bench-empty">
              暂无
              {boxFilter ? `货框「${boxFilter}」` : ''}
              {eventFilter === 'all' ? '' : eventFilter === 'alarm' ? '告警' : '碰撞'}
              事件
            </p>
          ) : null}
          <ul className="bench-event-list">
            {filteredEventGroups.map((group) => {
              const isActive = activeFrame?.frame_idx === group.frame_idx;
              return (
                <li key={group.key}>
                  <button
                    type="button"
                    className={`bench-event-group-btn${isActive ? ' is-active' : ''}`}
                    onClick={() => seekToFrameIdx(group.frame_idx)}
                  >
                    <div className="bench-event-group-head">
                      <strong>{formatTime(group.video_time_sec)}</strong>
                      <span>帧 {group.frame_idx}</span>
                    </div>
                    <ul className="bench-event-items">
                      {group.events.map((ev) => (
                        <li key={`${ev.kind}-${ev.token}`} className="bench-event-item">
                          <span className={`bench-event-kind is-${ev.kind}`}>
                            {ev.kind === 'alarm' ? '告警' : '碰撞'}
                          </span>
                          <span className="bench-event-box">货框 {ev.boxLabel}</span>
                        </li>
                      ))}
                    </ul>
                  </button>
                </li>
              );
            })}
          </ul>
        </aside>
      </div>
    </div>
  );
}
