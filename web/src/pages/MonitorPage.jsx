import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, Navigate, useSearchParams } from 'react-router-dom';
import AnnotateControls from '../components/AnnotateControls';
import InferenceToggle from '../components/InferenceToggle';
import MonitorPreviewStage from '../components/MonitorPreviewStage';
import ShelfBar from '../components/ShelfBar';
import ShelfDrawer, { emptyShelfForm } from '../components/ShelfDrawer';
import { useAnnotateTool } from '../features/annotate/useAnnotateTool';
import { boxMatchesAnyCollision, parseAnnotationPayload } from '../lib/annotation';
import { getPerspectiveTransform, perspectiveTransform } from '../lib/geometry';
import { apiGet, apiPost, cameraPlaybackUrl, openCameraLiveStream } from '../api/client';
import { resolveCameraModelLabel } from '../lib/cameraSettings';
import { formatInferenceMessage, formatUserError } from '../lib/userFacingText';
import './MonitorPage.css';

const EMPTY_ANNOTATION = {
  boxes: [],
  shelves: [],
  shelfCorners: [],
  annotationSize: null,
  gridShape: [],
};

const MAP_W = 600;
const MAP_H = 600;
const COCO_LINES = [
  [15, 13], [13, 11], [16, 14], [14, 12], [11, 12], [5, 11], [6, 12], [5, 6], [5, 7], [6, 8],
  [7, 9], [8, 10], [1, 2], [0, 1], [0, 2], [1, 3], [2, 4], [3, 5], [4, 6],
];

function getDist(p, a, b) {
  const l2 = (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2;
  if (l2 === 0) return Math.hypot(p[0] - a[0], p[1] - a[1]);
  const t = Math.max(0, Math.min(1, ((p[0] - a[0]) * (b[0] - a[0]) + (p[1] - a[1]) * (b[1] - a[1])) / l2));
  return Math.hypot(p[0] - (a[0] + t * (b[0] - a[0])), p[1] - (a[1] + t * (b[1] - a[1])));
}

export default function MonitorPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const cameraId = searchParams.get('camera')?.trim() || '';
  const viewMode = searchParams.get('mode') === 'annotate' ? 'annotate' : 'monitor';

  const setViewMode = useCallback(
    (mode) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (mode === 'annotate') next.set('mode', 'annotate');
          else next.delete('mode');
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  const [monitorCamera, setMonitorCamera] = useState(null);
  const [cameraLoadState, setCameraLoadState] = useState('loading');
  const [annotation, setAnnotation] = useState({
    boxes: [],
    shelves: [],
    shelfCorners: [],
    annotationSize: null,
    gridShape: [],
  });
  const [liveHits, setLiveHits] = useState([]);
  const [liveAlarms, setLiveAlarms] = useState([]);
  const [liveSkeletons, setLiveSkeletons] = useState([]);
  const [liveInferSize, setLiveInferSize] = useState({ w: 0, h: 0 });
  const [showVideoLayer, setShowVideoLayer] = useState(false);
  const [showSkeletonLayer, setShowSkeletonLayer] = useState(true);
  const [showRoiLayer, setShowRoiLayer] = useState(true);
  const [playback, setPlayback] = useState(null);

  useEffect(() => {
    setShowVideoLayer(false);
  }, [cameraId]);
  const [inferLoading, setInferLoading] = useState(false);
  const [shelfDrawerOpen, setShelfDrawerOpen] = useState(false);
  const [shelfDrawerForm, setShelfDrawerForm] = useState(emptyShelfForm);
  const [shelfDrawerSaving, setShelfDrawerSaving] = useState(false);
  const annotateCanvasRef = useRef(null);
  const applyAnnotationPayloadRef = useRef(() => false);
  const annotateTool = useAnnotateTool(annotateCanvasRef, {
    fixedCamera: monitorCamera
      ? { id: monitorCamera.id, name: monitorCamera.name, url: monitorCamera.url }
      : null,
    embedded: true,
    streamOverlay: true,
    canvasActive: viewMode === 'annotate',
  });
  applyAnnotationPayloadRef.current = annotateTool.applyAnnotationPayload;
  const [status, setStatus] = useState({
    title: '🟡 步骤 1：准备就绪',
    desc: '请先上传视频并完成货架网格标注，保存后再启动智能检测。',
    color: '#f39c12',
  });
  const [showAnnotator, setShowAnnotator] = useState(false);
  const [showViews, setShowViews] = useState(false);
  const [showHud, setShowHud] = useState(false);
  const [hud, setHud] = useState({ time: '00:00.000', frame: '0', fps: '0.0', hit: '0', alarm: '0', fpsDanger: false });

  const annCvsRef = useRef(null);
  const cvsRawRef = useRef(null);
  const cvsSkelRef = useRef(null);
  const cvsGridRef = useRef(null);
  const fileRef = useRef(null);
  const wsRef = useRef(null);

  const stateRef = useRef({
    bgImage: new Image(),
    shelfPoints: [],
    M_fwd: null,
    M_inv: null,
    gridRows: 4,
    gridCols: 4,
    layerYs: [],
    layerColXs: [],
    dragTarget: null,
    dragStartFlat: null,
    dragStartState: null,
    finalBoxes: [],
    annotationSaved: false,
  });

  const setUIStatus = useCallback((title, desc, color) => {
    setStatus({ title, desc, color });
  }, []);

  const initGrid = useCallback(() => {
    const s = stateRef.current;
    s.layerYs = [];
    s.layerColXs = [];
    for (let i = 0; i <= s.gridRows; i++) s.layerYs.push(i * (MAP_H / s.gridRows));
    for (let i = 0; i < s.gridRows; i++) {
      const cols = [];
      for (let j = 0; j <= s.gridCols; j++) cols.push(j * (MAP_W / s.gridCols));
      s.layerColXs.push(cols);
    }
  }, []);

  const renderAnnotator = useCallback(() => {
    const canvas = annCvsRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const s = stateRef.current;
    const { bgImage, shelfPoints, M_inv, gridRows, gridCols, layerYs, layerColXs } = s;

    ctx.drawImage(bgImage, 0, 0);
    ctx.lineWidth = 3;

    if (shelfPoints.length > 0) {
      ctx.strokeStyle = '#2ecc71';
      ctx.beginPath();
      ctx.moveTo(shelfPoints[0][0], shelfPoints[0][1]);
      for (let i = 1; i < shelfPoints.length; i++) ctx.lineTo(shelfPoints[i][0], shelfPoints[i][1]);
      if (shelfPoints.length === 4) ctx.closePath();
      ctx.stroke();
      ctx.fillStyle = '#e74c3c';
      for (const p of shelfPoints) {
        ctx.beginPath();
        ctx.arc(p[0], p[1], 6, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    if (M_inv) {
      ctx.strokeStyle = '#f1c40f';
      for (let i = 1; i < gridRows; i++) {
        const p1 = perspectiveTransform([0, layerYs[i]], M_inv);
        const p2 = perspectiveTransform([MAP_W, layerYs[i]], M_inv);
        ctx.beginPath();
        ctx.moveTo(p1[0], p1[1]);
        ctx.lineTo(p2[0], p2[1]);
        ctx.stroke();
      }
      for (let i = 0; i < gridRows; i++) {
        for (let j = 1; j < gridCols; j++) {
          const p1 = perspectiveTransform([layerColXs[i][j], layerYs[i]], M_inv);
          const p2 = perspectiveTransform([layerColXs[i][j], layerYs[i + 1]], M_inv);
          ctx.beginPath();
          ctx.moveTo(p1[0], p1[1]);
          ctx.lineTo(p2[0], p2[1]);
          ctx.stroke();
        }
      }
    }
  }, []);

  const resetAnnotationSession = useCallback(() => {
    const s = stateRef.current;
    s.shelfPoints = [];
    s.M_fwd = null;
    s.M_inv = null;
    s.layerYs = [];
    s.layerColXs = [];
    s.dragTarget = null;
    s.dragStartFlat = null;
    s.dragStartState = null;
    s.finalBoxes = [];
    s.annotationSaved = false;
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
  }, []);

  const syncAnnotationFromBackend = useCallback(async () => {
    try {
      const annData = await apiGet('/api/get_current_annotation');
      if (annData.status !== 'success' || !Array.isArray(annData.boxes) || !annData.boxes.length) {
        return false;
      }
      const s = stateRef.current;
      s.finalBoxes = annData.boxes;
      s.annotationSaved = true;
      if (Array.isArray(annData.grid_shape) && annData.grid_shape.length === 2) {
        const rows = Number(annData.grid_shape[0]);
        const cols = Number(annData.grid_shape[1]);
        if (rows > 0 && cols > 0) {
          s.gridRows = rows;
          s.gridCols = cols;
        }
      }
      return true;
    } catch {
      return false;
    }
  }, []);

  const buildFinalBoxesFromGrid = useCallback(() => {
    const s = stateRef.current;
    let boxId = 1;
    s.finalBoxes = [];
    for (let i = 0; i < s.gridRows; i++) {
      for (let j = 0; j < s.gridCols; j++) {
        s.finalBoxes.push({
          box_id: boxId++,
          layer: i + 1,
          column: j + 1,
          video_polygon: [
            perspectiveTransform([s.layerColXs[i][j], s.layerYs[i]], s.M_inv),
            perspectiveTransform([s.layerColXs[i][j + 1], s.layerYs[i]], s.M_inv),
            perspectiveTransform([s.layerColXs[i][j + 1], s.layerYs[i + 1]], s.M_inv),
            perspectiveTransform([s.layerColXs[i][j], s.layerYs[i + 1]], s.M_inv),
          ],
        });
      }
    }
  }, []);

  const startRenderEngine = useCallback(() => {
    const ctxRaw = cvsRawRef.current?.getContext('2d');
    const ctxSkel = cvsSkelRef.current?.getContext('2d');
    const ctxGrid = cvsGridRef.current?.getContext('2d');
    if (!ctxRaw || !ctxSkel || !ctxGrid) return;

    setShowHud(true);
    if (wsRef.current) wsRef.current.close();

    const ws = new WebSocket(`ws://${window.location.host}/ws/inference`);
    wsRef.current = ws;
    const streamImg = new Image();
    let hasReceivedFrame = false;
    const s = stateRef.current;

    ws.onmessage = (event) => {
      hasReceivedFrame = true;
      const data = JSON.parse(event.data);
      streamImg.src = `data:image/jpeg;base64,${data.image}`;
      const collisionSet = new Set(data.collisions || []);
      const alarmSet = new Set(data.alarm_collisions || []);

      setHud({
        time: data.stats.video_time,
        frame: String(data.stats.frame_idx),
        fps: String(data.stats.fps),
        hit: String(collisionSet.size),
        alarm: String(alarmSet.size),
        fpsDanger: data.stats.fps < 15,
      });

      streamImg.onload = () => {
        const coordScale = 640 / data.orig_width;
        ctxRaw.fillStyle = '#000';
        ctxRaw.fillRect(0, 0, 640, 360);
        ctxSkel.fillStyle = '#0d0d0d';
        ctxSkel.fillRect(0, 0, 640, 360);
        ctxRaw.drawImage(streamImg, 0, 0, 640, streamImg.height);
        ctxRaw.lineWidth = 2;

        s.finalBoxes.forEach((box) => {
          const isAlarmHit = boxMatchesAnyCollision(box, alarmSet);
          const isRawHit = !isAlarmHit && boxMatchesAnyCollision(box, collisionSet);
          ctxRaw.strokeStyle = isAlarmHit ? '#ff0000' : isRawHit ? '#ffd166' : 'rgba(0,255,0,0.25)';
          ctxRaw.beginPath();
          ctxRaw.moveTo(box.video_polygon[0][0] * coordScale, box.video_polygon[0][1] * coordScale);
          for (let i = 1; i < 4; i++) ctxRaw.lineTo(box.video_polygon[i][0] * coordScale, box.video_polygon[i][1] * coordScale);
          ctxRaw.closePath();
          ctxRaw.stroke();
        });

        ctxSkel.lineWidth = 2;
        s.finalBoxes.forEach((box) => {
          const isAlarmHit = boxMatchesAnyCollision(box, alarmSet);
          const isRawHit = !isAlarmHit && boxMatchesAnyCollision(box, collisionSet);
          ctxSkel.strokeStyle = isAlarmHit ? '#ff0000' : isRawHit ? '#ffd166' : 'rgba(0,255,0,0.2)';
          ctxSkel.beginPath();
          ctxSkel.moveTo(box.video_polygon[0][0] * coordScale, box.video_polygon[0][1] * coordScale);
          for (let i = 1; i < 4; i++) ctxSkel.lineTo(box.video_polygon[i][0] * coordScale, box.video_polygon[i][1] * coordScale);
          ctxSkel.closePath();
          ctxSkel.stroke();
        });

        (data.skeletons || []).forEach((person) => {
          const pts = person.keypoints;
          COCO_LINES.forEach((line) => {
            const p1 = pts[line[0]];
            const p2 = pts[line[1]];
            if (p1[2] > 0.3 && p2[2] > 0.3) {
              ctxSkel.strokeStyle = '#00e5ff';
              ctxSkel.beginPath();
              ctxSkel.moveTo(p1[0] * coordScale, p1[1] * coordScale);
              ctxSkel.lineTo(p2[0] * coordScale, p2[1] * coordScale);
              ctxSkel.stroke();
            }
          });
        });

        ctxGrid.fillStyle = '#1e1e1e';
        ctxGrid.fillRect(0, 0, 500, 735);
        const cw = 500 / s.gridCols;
        const ch = 735 / s.gridRows;
        s.finalBoxes.forEach((box) => {
          const x = (box.column - 1) * cw;
          const y = (box.layer - 1) * ch;
          const isAlarmHit = boxMatchesAnyCollision(box, alarmSet);
          const isRawHit = !isAlarmHit && boxMatchesAnyCollision(box, collisionSet);
          ctxGrid.fillStyle = isAlarmHit ? '#ff4757' : isRawHit ? '#f39c12' : '#3742fa';
          ctxGrid.fillRect(x + 2, y + 2, cw - 4, ch - 4);
          ctxGrid.strokeStyle = isAlarmHit ? '#ff6b81' : isRawHit ? '#f5b041' : '#5352ed';
          ctxGrid.lineWidth = 2;
          ctxGrid.strokeRect(x + 2, y + 2, cw - 4, ch - 4);
          ctxGrid.fillStyle = 'white';
          ctxGrid.font = 'bold 18px Arial';
          ctxGrid.fillText(`B${box.box_id}`, x + cw / 2 - 15, y + ch / 2 + 6);
        });
      };
    };

    ws.onclose = () => {
      setShowHud(false);
      setUIStatus(
        hasReceivedFrame ? '✅ 检测结束' : '⚠️ 检测未启动',
        hasReceivedFrame
          ? '视频已分析完成。可重新上传视频继续检测。'
          : '检测已结束。请确认已完成标注并已启动检测。',
        hasReceivedFrame ? '#2ecc71' : '#f39c12',
      );
      if (wsRef.current === ws) wsRef.current = null;
    };

    ws.onerror = () => {
      setUIStatus('❌ 连接异常', '与检测服务连接失败，请稍后重试。', '#e74c3c');
    };
  }, [setUIStatus]);

  const uploadAndStartAnnotating = async () => {
    const file = fileRef.current?.files?.[0];
    if (!file) return alert('请先选择视频文件！');
    resetAnnotationSession();
    setUIStatus('⏳ 正在处理', '正在上传视频并提取第一帧，请稍候...', '#f39c12');

    const fd = new FormData();
    fd.append('file', file);
    await fetch('/api/upload_video', { method: 'POST', credentials: 'include', body: fd });
    const data = await apiGet('/api/get_first_frame');
    if (data.error) return alert('提取视频帧失败！');

    const s = stateRef.current;
    s.bgImage.onload = () => {
      const canvas = annCvsRef.current;
      canvas.width = s.bgImage.width;
      canvas.height = s.bgImage.height;
      setShowAnnotator(true);
      setShowViews(false);
      setShowHud(false);
      setUIStatus(
        '🔵 步骤 2：标定货架外框',
        '请在下方的视频画面中，依次点击 4 个点（左上、右上、右下、左下），框选出整个货架的外侧边界。',
        '#3498db',
      );
      renderAnnotator();
    };
    s.bgImage.src = `data:image/jpeg;base64,${data.image}`;
  };

  const saveAnnotationOnly = async () => {
    const s = stateRef.current;
    if (s.shelfPoints.length < 4) return alert('请先标注4个点生成网格！');
    setUIStatus('🟠 步骤 4：保存标注', '正在保存当前货架网格配置…', '#f39c12');
    buildFinalBoxesFromGrid();
    const resp = await fetch('/api/save_annotation', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        shelf_corners: s.shelfPoints,
        grid_shape: [s.gridRows, s.gridCols],
        boxes: s.finalBoxes,
      }),
    });
    if (!resp.ok) {
      s.annotationSaved = false;
      setUIStatus('❌ 步骤 4 失败', '保存标注失败，请稍后重试。', '#e74c3c');
      return;
    }
    s.annotationSaved = true;
    setUIStatus('✅ 步骤 4 完成', '标注已保存。可点击「启动智能检测」开始分析。', '#2ecc71');
  };

  const startInferenceOnly = async () => {
    setUIStatus('🚀 步骤 5：正在启动检测', '正在加载检测模型，首次启动可能需等待片刻…', '#9b59b6');
    await syncAnnotationFromBackend();
    const resp = await fetch('/api/start_inference', { method: 'POST', credentials: 'include' });
    if (!resp.ok) {
      setUIStatus('❌ 步骤 5 失败', '启动检测失败，请确认已上传视频并完成标注保存。', '#e74c3c');
      return;
    }
    setShowAnnotator(false);
    setShowViews(true);
    setUIStatus(
      '🚨 步骤 5：实况检测中',
      '正在分析：左上为原视频与告警框，左下为人体姿态，右侧为货架俯视与碰撞高亮。',
      '#e74c3c',
    );
    startRenderEngine();
  };

  useEffect(() => {
    const canvas = annCvsRef.current;
    if (!canvas) return undefined;
    const s = stateRef.current;

    const onDown = (e) => {
      const rect = canvas.getBoundingClientRect();
      const scaleX = canvas.width / rect.width;
      const scaleY = canvas.height / rect.height;
      const x = (e.clientX - rect.left) * scaleX;
      const y = (e.clientY - rect.top) * scaleY;

      if (s.shelfPoints.length < 4) {
        s.shelfPoints.push([x, y]);
        if (s.shelfPoints.length === 4) {
          const dst = [
            [0, 0],
            [MAP_W, 0],
            [MAP_W, MAP_H],
            [0, MAP_H],
          ];
          s.M_fwd = getPerspectiveTransform(s.shelfPoints, dst);
          s.M_inv = getPerspectiveTransform(dst, s.shelfPoints);
          initGrid();
          setUIStatus(
            '🟢 步骤 3：独立微调网格线',
            '网格已自动贴合。请拖拽线条对齐货位边界，完成后保存标注，再启动检测。',
            '#2ecc71',
          );
        }
      } else {
        for (let i = 1; i < s.gridRows; i++) {
          const p1 = perspectiveTransform([0, s.layerYs[i]], s.M_inv);
          const p2 = perspectiveTransform([MAP_W, s.layerYs[i]], s.M_inv);
          if (getDist([x, y], p1, p2) < 15) {
            s.dragTarget = { type: 'h', i };
            s.dragStartFlat = perspectiveTransform([x, y], s.M_fwd);
            s.dragStartState = s.layerYs[i];
            return;
          }
        }
        for (let i = 0; i < s.gridRows; i++) {
          for (let j = 1; j < s.gridCols; j++) {
            const p1 = perspectiveTransform([s.layerColXs[i][j], s.layerYs[i]], s.M_inv);
            const p2 = perspectiveTransform([s.layerColXs[i][j], s.layerYs[i + 1]], s.M_inv);
            if (getDist([x, y], p1, p2) < 15) {
              s.dragTarget = { type: 'v', i, j };
              s.dragStartFlat = perspectiveTransform([x, y], s.M_fwd);
              s.dragStartState = s.layerColXs[i][j];
              return;
            }
          }
        }
      }
      renderAnnotator();
    };

    const onMove = (e) => {
      if (!s.dragTarget) return;
      const rect = canvas.getBoundingClientRect();
      const scaleX = canvas.width / rect.width;
      const scaleY = canvas.height / rect.height;
      const flatPt = perspectiveTransform(
        [(e.clientX - rect.left) * scaleX, (e.clientY - rect.top) * scaleY],
        s.M_fwd,
      );
      if (s.dragTarget.type === 'h') {
        const i = s.dragTarget.i;
        const dy = flatPt[1] - s.dragStartFlat[1];
        const newY = s.dragStartState + dy;
        s.layerYs[i] = Math.max(s.layerYs[i - 1] + 15, Math.min(s.layerYs[i + 1] - 15, newY));
      } else if (s.dragTarget.type === 'v') {
        const { i, j } = s.dragTarget;
        const dx = flatPt[0] - s.dragStartFlat[0];
        const newX = s.dragStartState + dx;
        s.layerColXs[i][j] = Math.max(s.layerColXs[i][j - 1] + 10, Math.min(s.layerColXs[i][j + 1] - 10, newX));
      }
      renderAnnotator();
    };

    const onUp = () => {
      s.dragTarget = null;
    };

    canvas.addEventListener('mousedown', onDown);
    canvas.addEventListener('mousemove', onMove);
    canvas.addEventListener('mouseup', onUp);
    return () => {
      canvas.removeEventListener('mousedown', onDown);
      canvas.removeEventListener('mousemove', onMove);
      canvas.removeEventListener('mouseup', onUp);
    };
  }, [initGrid, renderAnnotator, setUIStatus]);

  useEffect(() => {
    if (!cameraId) {
      setMonitorCamera(null);
      setCameraLoadState('loading');
      return undefined;
    }

    let cancelled = false;
    setMonitorCamera(null);
    setCameraLoadState('loading');

    (async () => {
      try {
        const camPath = `/api/cameras/${encodeURIComponent(cameraId)}?settings=0`;
        const annPath = `/api/cameras/${encodeURIComponent(cameraId)}/annotation`;
        const [data, annRes] = await Promise.all([
          apiGet(camPath),
          apiGet(annPath).catch(() => null),
        ]);
        if (cancelled) return;
        if (data.error || !data.camera) {
          setUIStatus('未找到摄像头', formatUserError(data.error) || '请从总览重新进入', '#e74c3c');
          setMonitorCamera(null);
          setCameraLoadState('error');
          return;
        }
        setMonitorCamera(data.camera);
        if (annRes?.status === 'success') {
          setAnnotation(parseAnnotationPayload(annRes));
          applyAnnotationPayloadRef.current(annRes, { silent: true });
        } else {
          setAnnotation(EMPTY_ANNOTATION);
        }
        setCameraLoadState('ready');
        apiGet(cameraPlaybackUrl(cameraId))
          .then((pb) => {
            if (!cancelled && pb?.status === 'success') setPlayback(pb);
          })
          .catch(() => {
            if (!cancelled) setPlayback(null);
          });
      } catch (e) {
        if (!cancelled) {
          setUIStatus('加载失败', formatUserError(e.message) || '无法加载摄像头', '#e74c3c');
          setMonitorCamera(null);
          setCameraLoadState('error');
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [cameraId, setUIStatus]);

  const refreshCameraMeta = useCallback(async () => {
    if (!cameraId) return;
    try {
      const data = await apiGet(`/api/cameras/${encodeURIComponent(cameraId)}?settings=0`);
      if (data.error || !data.camera) return;
      setMonitorCamera((prev) => ({ ...prev, ...data.camera }));
    } catch {
      /* ignore */
    }
  }, [cameraId]);

  const handleInferToggle = useCallback(
    async (turnOn) => {
      if (!cameraId || inferLoading) return;
      setInferLoading(true);
      try {
        const path = turnOn ? 'start' : 'stop';
        const data = await apiPost(
          `/api/cameras/${encodeURIComponent(cameraId)}/inference/${path}`,
          {},
        );
        if (data.error) {
          alert(formatUserError(data.error));
          return;
        }
        await refreshCameraMeta();
      } catch (e) {
        alert(formatUserError(e.message) || (turnOn ? '启动检测失败' : '停止检测失败'));
      } finally {
        setInferLoading(false);
      }
    },
    [cameraId, inferLoading, refreshCameraMeta],
  );

  const loadAnnotation = useCallback(async () => {
    if (!cameraId) return;
    try {
      const res = await apiGet(`/api/cameras/${encodeURIComponent(cameraId)}/annotation`);
      if (res.status === 'success') {
        setAnnotation(parseAnnotationPayload(res));
        applyAnnotationPayloadRef.current(res, { silent: true });
      } else {
        setAnnotation(EMPTY_ANNOTATION);
      }
    } catch {
      setAnnotation(EMPTY_ANNOTATION);
    }
  }, [cameraId]);

  const switchViewMode = useCallback(
    async (mode) => {
      if (viewMode === 'annotate' && mode === 'monitor') {
        await annotateTool.flushAutoSave();
        await loadAnnotation();
      }
      setViewMode(mode);
    },
    [viewMode, annotateTool, loadAnnotation, setViewMode],
  );

  useEffect(() => {
    if (!cameraId || viewMode !== 'annotate') return;
    loadAnnotation();
  }, [cameraId, viewMode, loadAnnotation]);

  const openShelfDrawer = useCallback(() => {
    setShelfDrawerForm(emptyShelfForm());
    setShelfDrawerOpen(true);
  }, []);

  const closeShelfDrawer = useCallback(() => {
    if (shelfDrawerSaving) return;
    setShelfDrawerOpen(false);
  }, [shelfDrawerSaving]);

  const handleShelfDrawerChange = useCallback((field, value) => {
    setShelfDrawerForm((prev) => ({ ...prev, [field]: value }));
  }, []);

  const confirmShelfDrawer = useCallback(async () => {
    setShelfDrawerSaving(true);
    try {
      const result = await annotateTool.createShelfFromDrawer(shelfDrawerForm);
      if (result?.ok) setShelfDrawerOpen(false);
    } finally {
      setShelfDrawerSaving(false);
    }
  }, [annotateTool, shelfDrawerForm]);

  const syncCanvasSizeRef = useRef(annotateTool.syncCanvasSize);
  syncCanvasSizeRef.current = annotateTool.syncCanvasSize;

  const handlePreviewFrameSize = useCallback(
    ({ width, height }) => {
      if (viewMode === 'annotate') {
        syncCanvasSizeRef.current(width, height);
      }
    },
    [viewMode],
  );

  useEffect(() => {
    if (!cameraId || viewMode !== 'monitor') return undefined;
    const running =
      monitorCamera?.inference?.status === 'running' ||
      monitorCamera?.inference?.status === 'starting';
    if (!running) {
      setLiveHits([]);
      setLiveAlarms([]);
      setLiveSkeletons([]);
      setLiveInferSize({ w: 0, h: 0 });
      return undefined;
    }

    let cancelled = false;

    const applyLiveFrame = (data) => {
      if (cancelled || !data || typeof data !== 'object') return;
      setLiveHits(Array.isArray(data.collisions) ? data.collisions : []);
      setLiveAlarms(Array.isArray(data.alarm_collisions) ? data.alarm_collisions : []);
      setLiveSkeletons(Array.isArray(data.skeletons) ? data.skeletons : []);
      setLiveInferSize({
        w: Number(data.infer_width) || 0,
        h: Number(data.infer_height) || 0,
      });
    };

    const closeStream = openCameraLiveStream(cameraId, {
      onFrame: applyLiveFrame,
      onError: () => {
        /* EventSource 会自动重连 */
      },
    });
    return () => {
      cancelled = true;
      closeStream();
    };
  }, [cameraId, viewMode, monitorCamera?.inference?.status]);

  useEffect(() => {
    if (!cameraId || monitorCamera?.inference?.status !== 'starting') return undefined;
    const timer = setInterval(refreshCameraMeta, 2000);
    return () => clearInterval(timer);
  }, [cameraId, monitorCamera?.inference?.status, refreshCameraMeta]);

  useEffect(() => {
    if (cameraId) return undefined;
    (async () => {
      try {
        const state = await apiGet('/api/runtime_state');
        if (state.status !== 'success' || state.source_type !== 'stream') return;
        if (!state.debug_visual_enabled) {
          setUIStatus(
            '✅ 网络摄像头检测中',
            '系统正在后台运行检测与告警，本页不显示实时画面。',
            '#2ecc71',
          );
          return;
        }
        const annOk = await syncAnnotationFromBackend();
        if (!annOk) {
          setUIStatus('⚠️ 待完成标注', '已接入网络摄像头，但尚未配置有效标注。', '#f39c12');
          return;
        }
        setShowAnnotator(false);
        setShowViews(true);
        setUIStatus('🚨 网络摄像头检测中', '已接入网络视频流，正在显示实时检测画面。', '#e74c3c');
        startRenderEngine();
      } catch {
        /* ignore */
      }
    })();
    return () => {
      if (wsRef.current) wsRef.current.close();
    };
  }, [cameraId, setUIStatus, startRenderEngine, syncAnnotationFromBackend]);

  const inferStatus = monitorCamera?.inference?.status || 'stopped';
  const inferRunning = inferStatus === 'running' || inferStatus === 'starting';
  const modelLabel = resolveCameraModelLabel(monitorCamera);

  if (!cameraId) {
    return <Navigate to="/" replace />;
  }

  return (
    <div className="page monitor-page monitor-page-camera">
      {monitorCamera ? (
        <header className="monitor-page-header">
          <h1 className="page-title">{monitorCamera.name}</h1>
          <div className="monitor-page-header-extra">
            <div className="monitor-chips">
              <span className={monitorCamera.online ? 'chip chip-ok' : 'chip chip-muted'}>
                {monitorCamera.online ? '在线' : '离线'}
              </span>
              <InferenceToggle
                on={inferRunning}
                loading={inferLoading}
                disabled={inferLoading || inferStatus === 'starting'}
                label={modelLabel}
                title={
                  formatInferenceMessage(monitorCamera.inference?.message) ||
                  (inferRunning ? `关闭 ${modelLabel} 检测` : `开启 ${modelLabel} 检测`)
                }
                onToggle={handleInferToggle}
              />
              {viewMode === 'monitor' ? (
                <div className="monitor-header-layers" role="group" aria-label="画面叠加层">
                  <label
                    className={`monitor-layer-switch${showVideoLayer ? ' on' : ''}`}
                    title="实时视频预览（WebRTC/HLS/MJPEG）"
                  >
                    <input
                      type="checkbox"
                      checked={showVideoLayer}
                      onChange={(e) => setShowVideoLayer(e.target.checked)}
                      aria-label="摄像头画面"
                    />
                    <span className="monitor-layer-switch-track" aria-hidden />
                    <span className="monitor-layer-switch-label">画面</span>
                  </label>
                  <label
                    className={`monitor-layer-switch${showSkeletonLayer ? ' on' : ''}`}
                    title="动作追踪（骨架）"
                  >
                    <input
                      type="checkbox"
                      checked={showSkeletonLayer}
                      onChange={(e) => setShowSkeletonLayer(e.target.checked)}
                      aria-label="动作追踪（骨架）"
                    />
                    <span className="monitor-layer-switch-track" aria-hidden />
                    <span className="monitor-layer-switch-label">骨架</span>
                  </label>
                  <label
                    className={`monitor-layer-switch${showRoiLayer ? ' on' : ''}`}
                    title="区域层（ROI）"
                  >
                    <input
                      type="checkbox"
                      checked={showRoiLayer}
                      onChange={(e) => setShowRoiLayer(e.target.checked)}
                      aria-label="区域层（ROI）"
                    />
                    <span className="monitor-layer-switch-track" aria-hidden />
                    <span className="monitor-layer-switch-label">ROI</span>
                  </label>
                </div>
              ) : null}
            </div>
            <div className="monitor-view-switch" role="tablist" aria-label="查看模式">
              <button
                type="button"
                role="tab"
                aria-selected={viewMode === 'monitor'}
                className={viewMode === 'monitor' ? 'active' : ''}
                onClick={() => switchViewMode('monitor')}
              >
                实时监控
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={viewMode === 'annotate'}
                className={viewMode === 'annotate' ? 'active' : ''}
                onClick={() => switchViewMode('annotate')}
              >
                标注
              </button>
            </div>
          </div>
        </header>
      ) : null}

      {cameraLoadState === 'loading' ? (
        <div className="monitor-load-state" role="status" aria-live="polite">
          <p className="monitor-load-state-title">正在加载摄像头…</p>
          <p className="monitor-load-state-desc">请稍候</p>
        </div>
      ) : null}

      {cameraLoadState === 'error' ? (
        <div className="monitor-load-state monitor-load-state--error" role="alert">
          <p className="monitor-load-state-title">{status.title}</p>
          <p className="monitor-load-state-desc">{status.desc}</p>
          <Link to="/" className="monitor-load-state-link">
            返回摄像头总览
          </Link>
        </div>
      ) : null}

      {monitorCamera ? (
        <MonitorPreviewStage
          cameraId={cameraId}
          playback={playback}
          boxes={annotation.boxes}
          shelves={annotation.shelves}
          gridShape={annotation.gridShape}
          shelfCorners={annotation.shelfCorners}
          annotationSize={annotation.annotationSize}
          inferRunning={inferRunning}
          hits={liveHits}
          alarms={liveAlarms}
          liveSkeletons={liveSkeletons}
          liveInferWidth={liveInferSize.w}
          liveInferHeight={liveInferSize.h}
          showVideoLayer={viewMode === 'annotate' ? true : showVideoLayer}
          showSkeletonLayer={showSkeletonLayer}
          showRoiLayer={showRoiLayer}
          annotateMode={viewMode === 'annotate'}
          annotateCanvasRef={annotateCanvasRef}
          onFrameSize={handlePreviewFrameSize}
          annotatePanel={<AnnotateControls tool={annotateTool} embedded />}
          shelfBar={
            viewMode === 'annotate' ? (
              <ShelfBar
                shelves={annotateTool.shelves}
                selectedCode={annotateTool.selectedShelfCode}
                showDraft={!annotateTool.selectedShelfCode}
                draftLabel={annotateTool.cameraName?.trim() || '新货架'}
                onSelect={annotateTool.switchShelfTab}
                onCreate={openShelfDrawer}
                onDelete={annotateTool.deleteSelectedShelf}
              />
            ) : (
              <ShelfBar readOnly shelves={annotation.shelves} />
            )
          }
        />
      ) : null}

      <ShelfDrawer
        open={shelfDrawerOpen}
        mode="create"
        form={shelfDrawerForm}
        onChange={handleShelfDrawerChange}
        onClose={closeShelfDrawer}
        onConfirm={confirmShelfDrawer}
        saving={shelfDrawerSaving}
      />
    </div>
  );
}
