import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { cameraStreamUrl } from '../api/client';
import { usePreviewStream } from '../hooks/usePreviewStream';
import { boxMatchesAnyCollision, boxRoiKey, resolveMonitorShelves } from '../lib/annotation';
import {
  computeContainLayout,
  mapPointsToVideoFrame,
  polygonToFramePoints,
} from '../lib/previewLayout';
import {
  STREAM_FORMATS,
  STREAM_HEIGHTS,
  heightLabel,
  loadStreamPrefs,
  saveStreamPrefs,
} from '../lib/streamPrefs';
import './MonitorPreviewStage.css';

const ROI_STATE = {
  configured: { label: '已配置', className: 'roi-configured' },
  monitoring: { label: '监测中', className: 'roi-monitoring' },
  hit: { label: '碰撞', className: 'roi-hit' },
  alarm: { label: '告警', className: 'roi-alarm' },
};

const FORMAT_LABELS = { mjpeg: 'MJPEG', hls: 'HLS', webrtc: 'WebRTC' };

const COCO_LINES = [
  [15, 13], [13, 11], [16, 14], [14, 12], [11, 12], [5, 11], [6, 12], [5, 6], [5, 7], [6, 8],
  [7, 9], [8, 10], [1, 2], [0, 1], [0, 2], [1, 3], [2, 4], [3, 5], [4, 6],
];

const SKELETON_CONF = 0.2;

function scaleInferPoint(x, y, inferW, inferH, frameW, frameH) {
  if (!inferW || !inferH) return [x, y];
  return [(x * frameW) / inferW, (y * frameH) / inferH];
}

function resolveRoiState(box, { inferRunning, hits, alarms }) {
  if (boxMatchesAnyCollision(box, alarms)) return 'alarm';
  if (boxMatchesAnyCollision(box, hits)) return 'hit';
  if (inferRunning) return 'monitoring';
  return 'configured';
}

function buildRoiMinimap(boxes, gridShape, { inferRunning, hits, alarms }) {
  let rows = 0;
  let cols = 0;
  if (Array.isArray(gridShape) && gridShape.length === 2) {
    rows = Number(gridShape[0]) || 0;
    cols = Number(gridShape[1]) || 0;
  }
  for (const box of boxes) {
    const layer = Number(box.layer);
    const column = Number(box.column);
    if (layer > 0) rows = Math.max(rows, layer);
    if (column > 0) cols = Math.max(cols, column);
  }
  if (!rows || !cols) {
    const count = boxes.length;
    if (!count) return { rows: 0, cols: 0, cells: [] };
    const side = Math.ceil(Math.sqrt(count));
    rows = side;
    cols = side;
  }

  const cellMap = new Map();
  for (const box of boxes) {
    const r = Number(box.layer) - 1;
    const c = Number(box.column) - 1;
    if (r < 0 || c < 0 || r >= rows || c >= cols) continue;
    const state = resolveRoiState(box, { inferRunning, hits, alarms });
    cellMap.set(`${r}-${c}`, {
      key: boxRoiKey(box) || `${box.layer}-${box.column}`,
      row: r,
      col: c,
      label: box.box_id != null ? String(box.box_id) : '',
      state,
      stateLabel: ROI_STATE[state].label,
      empty: false,
    });
  }

  const cells = [];
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const hit = cellMap.get(`${r}-${c}`);
      cells.push(
        hit || {
          key: `empty-${r}-${c}`,
          row: r,
          col: c,
          label: '',
          state: 'empty',
          stateLabel: '',
          empty: true,
        },
      );
    }
  }
  return { rows, cols, cells };
}

function shelfPanelTitle(shelf) {
  const name = String(shelf?.shelf_name || '').trim();
  const code = String(shelf?.shelf_code || '').trim();
  return name || code || '未命名货架';
}

function RoiMinimapGrid({ minimap, shelfLabel }) {
  if (!minimap.cells.length) {
    return <p className="monitor-roi-empty">该货架暂无货位，请在标注模式配置。</p>;
  }
  const aria = shelfLabel
    ? `${shelfLabel} 货位矩阵 ${minimap.rows} 行 ${minimap.cols} 列`
    : `货位矩阵 ${minimap.rows} 行 ${minimap.cols} 列`;
  return (
    <div className="monitor-roi-minimap-wrap">
      <div
        className="monitor-roi-minimap-fit"
        style={{
          '--minimap-cols': minimap.cols,
          '--minimap-rows': minimap.rows,
        }}
      >
        <div
          className="monitor-roi-minimap"
          style={{
            gridTemplateColumns: `repeat(${minimap.cols}, minmax(0, 1fr))`,
            gridTemplateRows: `repeat(${minimap.rows}, minmax(0, 1fr))`,
          }}
          role="img"
          aria-label={aria}
        >
          {minimap.cells.map((cell) => (
            <div
              key={cell.key}
              className={`monitor-roi-cell roi-${cell.state}`}
              title={
                cell.empty
                  ? `L${cell.row + 1} C${cell.col + 1} · 未配置`
                  : `L${cell.row + 1} C${cell.col + 1} · ${cell.label || '货位'} · ${cell.stateLabel}`
              }
            >
              <span className="monitor-roi-cell-id">{cell.label || '—'}</span>
            </div>
          ))}
        </div>
      </div>
      <div className="monitor-roi-minimap-axis">
        <span>L1→L{minimap.rows}</span>
        <span>C1→C{minimap.cols}</span>
      </div>
    </div>
  );
}

function RoiOverviewPanel({ legendItems, shelfPanels, hitCount = 0, alarmCount = 0, inferRunning = false }) {
  const hasCells = shelfPanels.some((p) => p.minimap.cells.length > 0);
  return (
    <div className="monitor-panel-content">
      {inferRunning ? (
        <div className="monitor-roi-live-stats" aria-live="polite">
          <span className={`monitor-roi-stat monitor-roi-stat-hit${hitCount ? ' active' : ''}`}>
            碰撞 {hitCount}
          </span>
          <span className={`monitor-roi-stat monitor-roi-stat-alarm${alarmCount ? ' active' : ''}`}>
            告警 {alarmCount}
          </span>
        </div>
      ) : null}
      <ul className="monitor-legend">
        {legendItems.map((item) => (
          <li key={item.className}>
            <span className={`legend-swatch ${item.className}`} />
            {item.label}
          </li>
        ))}
      </ul>
      {shelfPanels.length > 0 ? (
        <div className="monitor-roi-shelf-list">
          {shelfPanels.map((panel) => (
            <section key={panel.key} className="monitor-roi-shelf-section">
              <header className="monitor-roi-shelf-head">
                <span className="monitor-roi-shelf-title">{panel.title}</span>
                {panel.subtitle ? (
                  <span className="monitor-roi-shelf-code">{panel.subtitle}</span>
                ) : null}
              </header>
              <RoiMinimapGrid minimap={panel.minimap} shelfLabel={panel.title} />
            </section>
          ))}
        </div>
      ) : null}
      {!hasCells && (
        <p className="monitor-roi-empty">暂无 ROI，请切换到「标注」配置货位。</p>
      )}
    </div>
  );
}

export default function MonitorPreviewStage({
  cameraId,
  playback = null,
  imageSrc,
  boxes = [],
  shelves = [],
  gridShape = [],
  shelfCorners = [],
  annotationSize = null,
  inferRunning = false,
  hits = [],
  alarms = [],
  liveSkeletons = [],
  liveInferWidth = 0,
  liveInferHeight = 0,
  showVideoLayer = false,
  showSkeletonLayer = true,
  showRoiLayer = true,
  busy = false,
  emptyText = '暂无画面',
  annotateMode = false,
  annotateCanvasRef = null,
  annotatePanel = null,
  shelfBar = null,
  onFrameSize = null,
}) {
  const stageRef = useRef(null);
  const imgRef = useRef(null);
  const videoRef = useRef(null);
  const lastFrameSizeNotifyRef = useRef({ w: 0, h: 0 });
  const [layout, setLayout] = useState(null);
  const [frameSize, setFrameSize] = useState({ w: 0, h: 0 });
  const [streamPrefs, setStreamPrefs] = useState(() => loadStreamPrefs(cameraId));
  useEffect(() => {
    lastFrameSizeNotifyRef.current = { w: 0, h: 0 };
  }, [cameraId]);

  useEffect(() => {
    const prefs = loadStreamPrefs(cameraId);
    const hlsOk = Boolean(playback?.formats?.hls?.available);
    const rtcOk = Boolean(playback?.formats?.webrtc?.available);
    const ok =
      prefs.format === 'mjpeg' ||
      (prefs.format === 'hls' && hlsOk) ||
      (prefs.format === 'webrtc' && rtcOk);
    setStreamPrefs(ok ? prefs : { ...prefs, format: 'mjpeg' });
  }, [cameraId, playback]);

  const { format, height } = streamPrefs;
  const previewEnabled = Boolean(showVideoLayer && (cameraId || imageSrc));
  const mjpegSrc =
    previewEnabled && cameraId && format === 'mjpeg' ? cameraStreamUrl(cameraId, height) : '';
  const showVideo = format === 'hls' || format === 'webrtc';
  const hasMedia = previewEnabled && (showVideo || mjpegSrc || imageSrc);
  const overlayOnlyMode =
    !previewEnabled && !annotateMode && (showSkeletonLayer || showRoiLayer);
  const showStageContent = hasMedia || overlayOnlyMode;
  const stageEmptyText = !showVideoLayer ? '画面已关闭' : emptyText;

  const { streamError } = usePreviewStream({
    format,
    playback,
    mjpegSrc,
    videoRef,
    imgRef,
    enabled: previewEnabled,
  });

  const hitSet = useMemo(() => new Set(hits), [hits]);
  const alarmSet = useMemo(() => new Set(alarms), [alarms]);

  const updateLayout = useCallback(() => {
    const stage = stageRef.current;
    if (!stage) return;

    const applyFrame = (fw, fh, notifyFrameSize = true) => {
      if (!fw || !fh) return;
      setFrameSize({ w: fw, h: fh });
      const nextLayout = computeContainLayout(stage.clientWidth, stage.clientHeight, fw, fh);
      setLayout((prev) => {
        if (
          prev &&
          prev.frameW === nextLayout.frameW &&
          prev.frameH === nextLayout.frameH &&
          prev.drawW === nextLayout.drawW &&
          prev.drawH === nextLayout.drawH &&
          prev.offsetX === nextLayout.offsetX &&
          prev.offsetY === nextLayout.offsetY
        ) {
          return prev;
        }
        return nextLayout;
      });
      if (notifyFrameSize && onFrameSize) {
        const notifyW = annotateMode ? fw : annotationSize?.width || fw;
        const notifyH = annotateMode ? fh : annotationSize?.height || fh;
        const prev = lastFrameSizeNotifyRef.current;
        if (prev.w !== notifyW || prev.h !== notifyH) {
          lastFrameSizeNotifyRef.current = { w: notifyW, h: notifyH };
          onFrameSize({ width: notifyW, height: notifyH });
        }
      }
    };

    if (previewEnabled) {
      const el = showVideo ? videoRef.current : imgRef.current;
      if (!el) return;
      const nw = el.videoWidth || el.naturalWidth || 0;
      const nh = el.videoHeight || el.naturalHeight || 0;
      if (!nw) return;
      applyFrame(nw, nh);
      return;
    }

    if (annotateMode) return;

    if (!showSkeletonLayer && !showRoiLayer) {
      setLayout(null);
      return;
    }

    const fw = liveInferWidth || annotationSize?.width || 640;
    const fh = liveInferHeight || annotationSize?.height || 360;
    applyFrame(fw, fh, false);
  }, [
    annotationSize,
    annotateMode,
    liveInferHeight,
    liveInferWidth,
    onFrameSize,
    previewEnabled,
    showRoiLayer,
    showSkeletonLayer,
    showVideo,
  ]);

  useEffect(() => {
    const stage = stageRef.current;
    const main = stage?.parentElement;
    if (!stage) return undefined;
    const ro = new ResizeObserver(() => updateLayout());
    ro.observe(stage);
    if (main) ro.observe(main);
    return () => ro.disconnect();
  }, [updateLayout]);

  useEffect(() => {
    updateLayout();
  }, [
    mjpegSrc,
    format,
    imageSrc,
    annotateMode,
    overlayOnlyMode,
    showSkeletonLayer,
    showRoiLayer,
    liveInferWidth,
    liveInferHeight,
    annotationSize,
    updateLayout,
  ]);

  useEffect(() => {
    if (!showVideo) return undefined;
    const video = videoRef.current;
    if (!video) return undefined;
    const onResize = () => updateLayout();
    video.addEventListener('resize', onResize);
    const tick = setInterval(updateLayout, 500);
    return () => {
      video.removeEventListener('resize', onResize);
      clearInterval(tick);
    };
  }, [showVideo, updateLayout]);

  useEffect(() => {
    if (format !== 'mjpeg' || showVideo) return undefined;
    const tick = setInterval(updateLayout, 500);
    return () => clearInterval(tick);
  }, [format, showVideo, updateLayout]);

  const setFormat = (nextFormat) => {
    if (!STREAM_FORMATS.includes(nextFormat)) return;
    const next = { ...streamPrefs, format: nextFormat };
    setStreamPrefs(next);
    saveStreamPrefs(cameraId, next);
  };

  const setHeight = (nextHeight) => {
    const h = Number(nextHeight);
    if (!STREAM_HEIGHTS.includes(h)) return;
    const next = { ...streamPrefs, height: h };
    setStreamPrefs(next);
    saveStreamPrefs(cameraId, next);
  };

  const formatAvailable = (f) => {
    if (f === 'mjpeg') return true;
    return Boolean(playback?.formats?.[f]?.available);
  };

  const roiContext = useMemo(
    () => ({ inferRunning, hits: hitSet, alarms: alarmSet }),
    [inferRunning, hitSet, alarmSet],
  );

  const displayBoxes = useMemo(() => {
    if (annotateMode) return boxes;
    if (!showRoiLayer) return [];
    return boxes;
  }, [boxes, annotateMode, showRoiLayer]);

  const shelfPanels = useMemo(() => {
    const list = resolveMonitorShelves({ shelves, boxes });
    if (list.length) {
      return list.map((s) => {
        const code = String(s.shelf_code || '').trim();
        const title = shelfPanelTitle(s);
        const name = String(s.shelf_name || '').trim();
        return {
          key: code || title,
          shelfCode: code,
          title,
          subtitle: name && code && name !== title ? code : '',
          minimap: buildRoiMinimap(s.boxes || [], s.grid_shape, roiContext),
        };
      });
    }
    return [
      {
        key: 'all',
        shelfCode: '',
        title: '货位总览',
        subtitle: '',
        minimap: buildRoiMinimap(boxes, gridShape, roiContext),
      },
    ];
  }, [shelves, boxes, gridShape, roiContext]);

  const skeletonOverlay = useMemo(() => {
    if (annotateMode || !showSkeletonLayer || !layout || !liveSkeletons.length) {
      return null;
    }
    const fw = layout.frameW;
    const fh = layout.frameH;
    const inferW = liveInferWidth > 0 ? liveInferWidth : fw;
    const inferH = liveInferHeight > 0 ? liveInferHeight : fh;
    const scale = (x, y) => scaleInferPoint(x, y, inferW, inferH, fw, fh);
    return liveSkeletons.map((person, pi) => {
      const pts = person?.keypoints;
      if (!Array.isArray(pts) || !pts.length) return null;
      const lines = [];
      COCO_LINES.forEach((line, li) => {
        const p1 = pts[line[0]];
        const p2 = pts[line[1]];
        if (!p1 || !p2 || p1[2] <= SKELETON_CONF || p2[2] <= SKELETON_CONF) return;
        const [x1, y1] = scale(p1[0], p1[1]);
        const [x2, y2] = scale(p2[0], p2[1]);
        lines.push(
          <line
            key={`${pi}-${li}`}
            className="roi-skeleton-line"
            x1={x1}
            y1={y1}
            x2={x2}
            y2={y2}
          />,
        );
      });
      return lines.length ? <g key={`skel-${pi}`}>{lines}</g> : null;
    });
  }, [annotateMode, showSkeletonLayer, layout, liveSkeletons, liveInferWidth, liveInferHeight]);

  const shelfOutlines = useMemo(() => {
    if (!showRoiLayer && !annotateMode) return [];
    if (Array.isArray(shelves) && shelves.length) {
      return shelves.filter(
        (s) => Array.isArray(s.shelf_corners) && s.shelf_corners.length >= 3,
      );
    }
    if (shelfCorners.length >= 3) {
      return [{ shelf_code: '', shelf_corners: shelfCorners }];
    }
    return [];
  }, [shelves, shelfCorners, showRoiLayer, annotateMode]);

  const multiShelf = Array.isArray(shelves) && shelves.length > 1;

  const legendItems = [
    ROI_STATE.configured,
    ROI_STATE.monitoring,
    ROI_STATE.hit,
    ROI_STATE.alarm,
  ];

  const panelModeLabel = annotateMode ? '标注' : '监控';
  const panelBody = annotateMode ? annotatePanel : (
    <RoiOverviewPanel
      legendItems={legendItems}
      shelfPanels={shelfPanels}
      hitCount={hits.length}
      alarmCount={alarms.length}
      inferRunning={inferRunning}
    />
  );

  return (
    <div className="monitor-stage">
      <div className="monitor-stage-main">
        <div className={`monitor-stage-viewport${busy && !showStageContent ? ' is-busy' : ''}`} ref={stageRef}>
        {cameraId && showVideoLayer ? (
          <div className="monitor-stream-bar" role="group" aria-label="实时预览设置">
            <select
              className="monitor-stream-select"
              value={height}
              onChange={(e) => setHeight(Number(e.target.value))}
              disabled={format !== 'mjpeg'}
              title={format === 'mjpeg' ? '分辨率（MJPEG）' : '分辨率仅对 MJPEG 生效'}
            >
              {STREAM_HEIGHTS.map((h) => (
                <option key={h} value={h}>
                  {heightLabel(h)}
                </option>
              ))}
            </select>
            <select
              className="monitor-stream-select"
              value={format}
              onChange={(e) => setFormat(e.target.value)}
              title="传输格式"
            >
              {STREAM_FORMATS.map((f) => (
                <option key={f} value={f} disabled={!formatAvailable(f)}>
                  {FORMAT_LABELS[f]}
                </option>
              ))}
            </select>
          </div>
        ) : null}

        {showStageContent ? (
          <>
            <div className="monitor-media-fit">
              <div
                className={`monitor-media-fit-inner${layout ? '' : ' is-loading'}${annotateMode ? ' is-annotate' : ''}${overlayOnlyMode ? ' is-overlay-only' : ''}`}
                style={
                  layout
                    ? { width: `${layout.drawW}px`, height: `${layout.drawH}px` }
                    : undefined
                }
              >
                {previewEnabled ? (
                  <>
                    <video
                      ref={videoRef}
                      className={`monitor-stage-media-fit${showVideo ? '' : ' is-hidden'}`}
                      autoPlay
                      muted
                      playsInline
                      onLoadedMetadata={updateLayout}
                      onLoadedData={updateLayout}
                      onPlaying={updateLayout}
                    />
                    <img
                      ref={imgRef}
                      src={imageSrc || undefined}
                      alt=""
                      className={`monitor-stage-media-fit${showVideo ? ' is-hidden' : ''}`}
                      onLoad={updateLayout}
                    />
                  </>
                ) : null}
                {annotateMode && annotateCanvasRef ? (
                  <canvas
                    ref={(node) => {
                      annotateCanvasRef.current = node;
                    }}
                    className="monitor-annotate-overlay"
                    aria-hidden={false}
                  />
                ) : null}
                {layout && !annotateMode ? (
                  <svg
                    className="monitor-stage-svg"
                    viewBox={`0 0 ${layout.frameW} ${layout.frameH}`}
                    preserveAspectRatio="none"
                  >
                    {shelfOutlines.map((shelf) => (
                      <polygon
                        key={shelf.shelf_code || 'shelf-outline'}
                        className="roi-shelf-outline"
                        points={polygonToFramePoints(
                          shelf.shelf_corners,
                          annotationSize || frameSize,
                          null,
                          layout.frameW,
                          layout.frameH,
                        )}
                      />
                    ))}
                    {showRoiLayer
                      ? displayBoxes.map((box) => {
                          const state = resolveRoiState(box, {
                            inferRunning,
                            hits: hitSet,
                            alarms: alarmSet,
                          });
                          const ann = annotationSize || frameSize;
                          const pts = polygonToFramePoints(
                            box.video_polygon,
                            ann,
                            box.video_polygon_norm,
                            layout.frameW,
                            layout.frameH,
                          );
                          const framePoly = mapPointsToVideoFrame(
                            box.video_polygon,
                            box.video_polygon_norm,
                            ann,
                            layout.frameW,
                            layout.frameH,
                          );
                          const cx =
                            framePoly.reduce((s, p) => s + p[0], 0) / (framePoly.length || 1);
                          const cy =
                            framePoly.reduce((s, p) => s + p[1], 0) / (framePoly.length || 1);
                          return (
                            <g
                              key={boxRoiKey(box) || `${box.layer}-${box.column}`}
                              className={`roi-shape ${ROI_STATE[state].className}`}
                            >
                              <polygon points={pts} />
                              <text x={cx} y={cy} className="roi-label">
                                {box.box_id != null
                                  ? multiShelf && box.shelf_code
                                    ? `${box.shelf_code}:${box.box_id}`
                                    : String(box.box_id)
                                  : ''}
                              </text>
                            </g>
                          );
                        })
                      : null}
                    {skeletonOverlay}
                  </svg>
                ) : null}
              </div>
            </div>
            {streamError ? <div className="monitor-stream-hint">{streamError}</div> : null}
            {!annotateMode && showSkeletonLayer && inferRunning && !liveSkeletons.length ? (
              <div className="monitor-stream-hint">骨架已开启，等待检测姿态数据…</div>
            ) : null}
          </>
        ) : (
          <div className="monitor-stage-empty">{busy ? '正在加载画面…' : stageEmptyText}</div>
        )}
        </div>
        {shelfBar ? <div className="monitor-stage-shelf-slot">{shelfBar}</div> : null}
      </div>

      <aside
        id="monitor-side-panel"
        className="monitor-side-panel"
        aria-labelledby="monitor-panel-drawer-title"
      >
        <header className="monitor-side-panel-header">
          <div className="monitor-panel-drawer-heading">
            <h2 id="monitor-panel-drawer-title">面板</h2>
            <span className="monitor-panel-mode-tag">{panelModeLabel}</span>
          </div>
        </header>
        <div className="monitor-panel-drawer-body">{panelBody}</div>
      </aside>
    </div>
  );
}
