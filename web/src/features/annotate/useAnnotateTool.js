import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { apiDelete, apiGet, apiPost } from '../../api/client.js';
import {
  getPerspectiveTransform,
  getPointDist,
  insetConvexQuad,
  perspectiveTransform,
  pointInPolygon,
} from '../../lib/geometry.js';
import { formatUserError } from '../../lib/userFacingText.js';
import { isNormPolygonValid, mapPointsToVideoFrame } from '../../lib/previewLayout.js';

const MAP_W = 600;
const MAP_H = 600;
/** 自动生成货位时相对网格格子的内缩比例 */
const CELL_POLYGON_INSET = 0.08;
const DEFAULT_CANVAS_W = 854;
const DEFAULT_CANVAS_H = 480;

const INITIAL_STATUS_HTML = [
  '1. 每个货架会自动生成区域四边形，拖动顶点即可调整。',
  '2. 选择网格行列后点击「确认生成」创建货位。',
  '3. 可选择已保存的摄像头，或新增后抓取一帧进行标注。',
  '4. 完成后点击「保存标注」。',
].join('<br/>');

/** 默认货架四边形：左上、右上、右下、左下 */
function createDefaultShelfCorners(frameW, frameH, marginRatio = 0.12) {
  const w = Math.max(1, Number(frameW) || 1);
  const h = Math.max(1, Number(frameH) || 1);
  const mx = w * marginRatio;
  const my = h * marginRatio;
  return [
    [mx, my],
    [w - mx, my],
    [w - mx, h - my],
    [mx, h - my],
  ];
}

function getCellKey(rowIdx, colIdx) {
  return `${rowIdx + 1}-${colIdx + 1}`;
}

function averageArray(values) {
  let sum = 0;
  for (const val of values) sum += val;
  return sum / values.length;
}

function clonePoly(poly) {
  return poly.map(([x, y]) => [Number(x), Number(y)]);
}

function parseCellKey(key) {
  const parts = String(key).split('-');
  if (parts.length !== 2) return null;
  const rowIdx = Number(parts[0]) - 1;
  const colIdx = Number(parts[1]) - 1;
  if (!Number.isInteger(rowIdx) || !Number.isInteger(colIdx) || rowIdx < 0 || colIdx < 0) {
    return null;
  }
  return { rowIdx, colIdx };
}

export function useAnnotateTool(canvasRef, options = {}) {
  const {
    fixedCamera = null,
    embedded = false,
    streamOverlay = false,
    /** 监控页标注 canvas 随 Tab 挂载/卸载，需为 true 时才绑定指针事件 */
    canvasActive = true,
    /** 从 HLS/WebRTC 预览 video 抓帧，返回 base64（不含 data: 前缀） */
    getBrowserCapture = null,
  } = options;
  const fixedCameraRef = useRef(fixedCamera);
  fixedCameraRef.current = fixedCamera;
  const videoFileRef = useRef(null);
  const bgImageRef = useRef(null);
  const shelfPointsRef = useRef([]);
  const mFwdRef = useRef(null);
  const mInvRef = useRef(null);
  const layerYsRef = useRef([]);
  const layerColXsRef = useRef([]);
  /** @type {React.MutableRefObject<Map<string, number[][]>>} */
  const cellPolygonsRef = useRef(new Map());
  /** @type {React.MutableRefObject<Set<string>>} */
  const deletedCellsRef = useRef(new Set());
  const dragTargetRef = useRef(null);
  const dragStartFlatRef = useRef(null);
  const dragStartStateRef = useRef(null);
  const cameraNameByUrlRef = useRef({});
  const loadedAnnotationRef = useRef(null);
  const annotationSourceRef = useRef({
    capture_source: 'file',
    camera_url: '',
    camera_name: '',
    shelf_code: '',
  });
  const activeShelfIndexRef = useRef(-1);
  const autoSaveTimerRef = useRef(null);
  const autoSaveRef = useRef({ immediate: async () => ({ ok: true }), schedule: () => {} });
  const coordSpaceRef = useRef({ w: 0, h: 0 });
  const annotationSizeRef = useRef(null);
  const shelvesRef = useRef([]);
  const selectedShelfCodeRef = useRef('');
  const annotationBootstrappedRef = useRef('');
  const [renderTick, setRenderTick] = useState(0);
  const bumpRender = useCallback(() => setRenderTick((n) => n + 1), []);

  const [statusHtml, setStatusHtml] = useState(INITIAL_STATUS_HTML);
  const [statusClass, setStatusClass] = useState('');
  const [gridRows, setGridRows] = useState(4);
  const [gridCols, setGridCols] = useState(4);
  const gridRowsRef = useRef(4);
  const gridColsRef = useRef(4);
  const [cameraIpList, setCameraIpList] = useState([]);
  const [cameraIp, setCameraIp] = useState('');
  const [cameraName, setCameraName] = useState('');
  const [shelves, setShelves] = useState([]);

  useEffect(() => {
    shelvesRef.current = shelves;
  }, [shelves]);
  const [selectedShelfCode, setSelectedShelfCode] = useState('');
  useEffect(() => {
    selectedShelfCodeRef.current = selectedShelfCode;
  }, [selectedShelfCode]);
  useEffect(() => {
    gridRowsRef.current = gridRows;
    gridColsRef.current = gridCols;
  }, [gridRows, gridCols]);
  const [boxIdOverrides, setBoxIdOverrides] = useState({});
  const [selectedCell, setSelectedCell] = useState(null);
  const [canSave, setCanSave] = useState(false);

  const setStatus = useCallback((text, cls) => {
    setStatusHtml(text);
    setStatusClass(cls || '');
  }, []);

  const getCanvas = useCallback(() => canvasRef?.current ?? null, [canvasRef]);

  const getCtx = useCallback(() => {
    const cvs = getCanvas();
    return cvs ? cvs.getContext('2d') : null;
  }, [getCanvas]);

  const getDefaultBoxId = useCallback(
    (rowIdx, colIdx) => String(rowIdx * gridCols + colIdx + 1),
    [gridCols],
  );

  const getEffectiveBoxId = useCallback(
    (rowIdx, colIdx) => {
      const key = getCellKey(rowIdx, colIdx);
      const defaultId = getDefaultBoxId(rowIdx, colIdx);
      const raw = boxIdOverrides[key];
      if (raw === undefined || raw === null || raw === '') {
        return defaultId;
      }
      const text = String(raw).trim();
      if (!text) {
        return defaultId;
      }
      return text;
    },
    [boxIdOverrides, getDefaultBoxId],
  );

  const initGrid = useCallback(
    (rowsOverride, colsOverride) => {
      const rows = rowsOverride ?? gridRows;
      const cols = colsOverride ?? gridCols;
      const layerYs = [];
      const layerColXs = [];
      for (let i = 0; i <= rows; i++) layerYs.push(i * (MAP_H / rows));
      for (let i = 0; i < rows; i++) {
        const colLine = [];
        for (let j = 0; j <= cols; j++) colLine.push(j * (MAP_W / cols));
        layerColXs.push(colLine);
      }
      layerYsRef.current = layerYs;
      layerColXsRef.current = layerColXs;
    },
    [gridRows, gridCols],
  );

  const getGridExtent = useCallback(() => {
    const ys = layerYsRef.current;
    const colXs = layerColXsRef.current;
    let divR = ys.length > 1 ? ys.length - 1 : 0;
    let divC = 0;
    for (const row of colXs) {
      if (row?.length > 1) divC = Math.max(divC, row.length - 1);
    }
    return {
      rows: Math.max(gridRows, divR),
      cols: Math.max(gridCols, divC),
    };
  }, [gridRows, gridCols]);

  const computeCellPolyFromGrid = useCallback((rowIdx, colIdx) => {
    const M_inv = mInvRef.current;
    const layerYs = layerYsRef.current;
    const layerColXs = layerColXsRef.current;
    if (!M_inv || !layerColXs[rowIdx] || layerColXs[rowIdx].length <= colIdx + 1) {
      return null;
    }
    const p0 = perspectiveTransform([layerColXs[rowIdx][colIdx], layerYs[rowIdx]], M_inv);
    const p1 = perspectiveTransform([layerColXs[rowIdx][colIdx + 1], layerYs[rowIdx]], M_inv);
    const p2 = perspectiveTransform([layerColXs[rowIdx][colIdx + 1], layerYs[rowIdx + 1]], M_inv);
    const p3 = perspectiveTransform([layerColXs[rowIdx][colIdx], layerYs[rowIdx + 1]], M_inv);
    return insetConvexQuad(clonePoly([p0, p1, p2, p3]), CELL_POLYGON_INSET);
  }, []);

  const getCellPoly = useCallback(
    (rowIdx, colIdx) => {
      const key = getCellKey(rowIdx, colIdx);
      if (deletedCellsRef.current.has(key)) {
        return null;
      }
      const existing = cellPolygonsRef.current.get(key);
      if (existing && existing.length >= 4) {
        return existing;
      }
      const computed = computeCellPolyFromGrid(rowIdx, colIdx);
      if (computed) {
        cellPolygonsRef.current.set(key, computed);
      }
      return computed;
    },
    [computeCellPolyFromGrid],
  );

  const fillMissingCellPolygons = useCallback(
    (fromRow = 0, fromCol = 0) => {
      const { rows, cols } = getGridExtent();
      for (let i = fromRow; i < rows; i++) {
        for (let j = fromCol; j < cols; j++) {
          const key = getCellKey(i, j);
          if (deletedCellsRef.current.has(key)) continue;
          if (!cellPolygonsRef.current.has(key)) {
            const poly = computeCellPolyFromGrid(i, j);
            if (poly) cellPolygonsRef.current.set(key, poly);
          }
        }
      }
    },
    [getGridExtent, computeCellPolyFromGrid],
  );

  const markUnmappedCellsDeleted = useCallback(() => {
    deletedCellsRef.current = new Set();
    const { rows, cols } = getGridExtent();
    for (let i = 0; i < rows; i++) {
      for (let j = 0; j < cols; j++) {
        const key = getCellKey(i, j);
        if (!cellPolygonsRef.current.has(key)) {
          deletedCellsRef.current.add(key);
        }
      }
    }
  }, [getGridExtent]);

  const loadCellPolygonsFromBoxes = useCallback((boxes) => {
    cellPolygonsRef.current = new Map();
    deletedCellsRef.current = new Set();
    if (!Array.isArray(boxes)) return;
    for (const box of boxes) {
      const rowIdx = Number(box.layer) - 1;
      const colIdx = Number(box.column) - 1;
      if (rowIdx < 0 || colIdx < 0) continue;
      const poly = box.video_polygon;
      if (!Array.isArray(poly) || poly.length < 4) continue;
      cellPolygonsRef.current.set(getCellKey(rowIdx, colIdx), clonePoly(poly.slice(0, 4)));
    }
  }, []);

  const expandGridPreserving = useCallback((oldRows, oldCols, newRows, newCols) => {
    const oldYs = [...layerYsRef.current];
    const oldColXs = layerColXsRef.current.map((row) => [...row]);
    initGrid(newRows, newCols);
    const newYs = layerYsRef.current;
    const newColXs = layerColXsRef.current;
    for (let i = 0; i <= oldRows && i < newYs.length; i++) {
      if (oldYs[i] !== undefined) newYs[i] = oldYs[i];
    }
    for (let i = 0; i < oldRows && i < newRows; i++) {
      for (let j = 0; j <= oldCols && j < (newColXs[i]?.length ?? 0); j++) {
        if (oldColXs[i]?.[j] !== undefined) {
          newColXs[i][j] = oldColXs[i][j];
        }
      }
    }
    layerYsRef.current = newYs;
    layerColXsRef.current = newColXs;
  }, [initGrid]);

  const refreshPerspectiveFromShelf = useCallback(() => {
    const shelfPoints = shelfPointsRef.current;
    if (shelfPoints.length !== 4) return;
    const dst = [
      [0, 0],
      [MAP_W, 0],
      [MAP_W, MAP_H],
      [0, MAP_H],
    ];
    try {
      mFwdRef.current = getPerspectiveTransform(shelfPoints, dst);
      mInvRef.current = getPerspectiveTransform(dst, shelfPoints);
    } catch {
      // 跳过退化状态，避免拖动过程中偶发不可逆。
    }
  }, []);

  const renderEmptyCanvas = useCallback(() => {
    const annCvs = getCanvas();
    const annCtx = getCtx();
    if (!annCvs || !annCtx) return;
    annCtx.clearRect(0, 0, annCvs.width, annCvs.height);
    if (streamOverlay) return;
    annCtx.fillStyle = '#0f151a';
    annCtx.fillRect(0, 0, annCvs.width, annCvs.height);
    annCtx.fillStyle = '#7f8c8d';
    annCtx.font = 'bold 18px Arial';
    annCtx.textAlign = 'center';
    annCtx.textBaseline = 'middle';
    annCtx.fillText('等待载入标注或视频帧...', annCvs.width / 2, annCvs.height / 2);
  }, [getCanvas, getCtx, streamOverlay]);

  const drawShelfOutline = useCallback(() => {
    const annCtx = getCtx();
    const shelfPoints = shelfPointsRef.current;
    if (!annCtx || !Array.isArray(shelfPoints) || shelfPoints.length === 0) return;
    annCtx.lineWidth = 3;
    annCtx.strokeStyle = '#2ecc71';
    annCtx.beginPath();
    annCtx.moveTo(shelfPoints[0][0], shelfPoints[0][1]);
    for (let i = 1; i < shelfPoints.length; i++) {
      annCtx.lineTo(shelfPoints[i][0], shelfPoints[i][1]);
    }
    if (shelfPoints.length === 4) annCtx.closePath();
    annCtx.stroke();

    annCtx.fillStyle = '#e74c3c';
    for (const p of shelfPoints) {
      annCtx.beginPath();
      annCtx.arc(p[0], p[1], 6, 0, Math.PI * 2);
      annCtx.fill();
    }
  }, [getCtx]);

  const drawAnnotationBoxes = useCallback(
    (ann, options) => {
      const annCvs = getCanvas();
      const annCtx = getCtx();
      if (!annCvs || !annCtx) return;
      const opts = options || {};
      const overlay = opts.overlay === true;

      if (!overlay) {
        annCtx.clearRect(0, 0, annCvs.width, annCvs.height);
        annCtx.fillStyle = '#0f151a';
        annCtx.fillRect(0, 0, annCvs.width, annCvs.height);
      }

      const boxes = Array.isArray(ann.boxes) ? ann.boxes : [];
      if (!boxes.length) return;

      annCtx.lineWidth = 2;
      annCtx.strokeStyle = overlay ? '#00d2a0' : '#f1c40f';
      annCtx.fillStyle = '#00ffcc';
      annCtx.font = overlay ? 'bold 16px Arial' : 'bold 18px Arial';
      annCtx.textAlign = 'center';
      annCtx.textBaseline = 'middle';

      for (const box of boxes) {
        const poly = Array.isArray(box.video_polygon) ? box.video_polygon : [];
        if (poly.length < 4) continue;

        annCtx.beginPath();
        annCtx.moveTo(poly[0][0], poly[0][1]);
        for (let i = 1; i < poly.length; i++) {
          annCtx.lineTo(poly[i][0], poly[i][1]);
        }
        annCtx.closePath();
        annCtx.stroke();

        let sumX = 0;
        let sumY = 0;
        let count = 0;
        for (const pt of poly) {
          sumX += pt[0];
          sumY += pt[1];
          count += 1;
        }
        const centerX = count ? sumX / count : poly[0][0];
        const centerY = count ? sumY / count : poly[0][1];
        const label = box.box_id !== undefined ? String(box.box_id) : '';
        if (label) {
          annCtx.fillText(label, centerX, centerY);
        }
      }
    },
    [getCanvas, getCtx],
  );

  const drawEditableGridBoxes = useCallback(() => {
    const annCtx = getCtx();
    if (!annCtx || !mInvRef.current) return;

    annCtx.textAlign = 'center';
    annCtx.textBaseline = 'middle';
    annCtx.font = 'bold 16px Arial';

    const { rows: extentRows, cols: extentCols } = getGridExtent();
    const handleR = 5;
    const selRow = selectedCell?.rowIdx;
    const selCol = selectedCell?.colIdx;

    for (let i = 0; i < extentRows; i++) {
      for (let j = 0; j < extentCols; j++) {
        const poly = getCellPoly(i, j);
        if (!poly || poly.length < 4) continue;

        const isSelected = selRow === i && selCol === j;
        annCtx.strokeStyle = isSelected ? '#00d4aa' : 'rgba(241, 196, 15, 0.45)';
        annCtx.lineWidth = isSelected ? 2.5 : 1.5;
        if (isSelected) {
          annCtx.fillStyle = 'rgba(0, 212, 170, 0.12)';
          annCtx.beginPath();
          annCtx.moveTo(poly[0][0], poly[0][1]);
          for (let k = 1; k < poly.length; k++) {
            annCtx.lineTo(poly[k][0], poly[k][1]);
          }
          annCtx.closePath();
          annCtx.fill();
        }

        annCtx.beginPath();
        annCtx.moveTo(poly[0][0], poly[0][1]);
        for (let k = 1; k < poly.length; k++) {
          annCtx.lineTo(poly[k][0], poly[k][1]);
        }
        annCtx.closePath();
        annCtx.stroke();

        if (!isSelected) continue;

        annCtx.fillStyle = '#e67e22';
        for (const pt of poly) {
          annCtx.beginPath();
          annCtx.arc(pt[0], pt[1], handleR, 0, Math.PI * 2);
          annCtx.fill();
        }

        const centerX = poly.reduce((s, p) => s + p[0], 0) / poly.length;
        const centerY = poly.reduce((s, p) => s + p[1], 0) / poly.length;
        annCtx.fillStyle = '#00ffcc';
        annCtx.fillText(getEffectiveBoxId(i, j), centerX, centerY);
      }
    }
    annCtx.lineWidth = 3;
  }, [getCtx, getGridExtent, getCellPoly, getEffectiveBoxId, selectedCell]);

  const renderLoadedAnnotation = useCallback(() => {
    const ann = loadedAnnotationRef.current;
    if (!ann) return;
    drawAnnotationBoxes(ann, { overlay: false });
    drawShelfOutline();
  }, [drawAnnotationBoxes, drawShelfOutline]);

  const renderAnnotator = useCallback(() => {
    const annCvs = getCanvas();
    const annCtx = getCtx();
    const bgImage = bgImageRef.current;
    if (!annCvs || !annCtx) return;

    if (streamOverlay) {
      annCtx.clearRect(0, 0, annCvs.width, annCvs.height);
      if (shelfPointsRef.current.length > 0) {
        annCtx.lineWidth = 3;
        drawShelfOutline();
      }
      if (mInvRef.current) {
        drawEditableGridBoxes();
      }
      return;
    }

    if (!bgImage?.src) {
      if (loadedAnnotationRef.current) {
        renderLoadedAnnotation();
        if (mInvRef.current) {
          drawEditableGridBoxes();
        }
      } else if (mInvRef.current) {
        renderEmptyCanvas();
        drawEditableGridBoxes();
      } else {
        renderEmptyCanvas();
      }
      return;
    }

    annCtx.drawImage(bgImage, 0, 0);
    annCtx.lineWidth = 3;

    if (shelfPointsRef.current.length > 0) {
      drawShelfOutline();
    }

    if (mInvRef.current) {
      drawEditableGridBoxes();
    }
  }, [
    getCanvas,
    getCtx,
    streamOverlay,
    renderLoadedAnnotation,
    renderEmptyCanvas,
    drawEditableGridBoxes,
    drawShelfOutline,
  ]);

  const initCanvasPlaceholder = useCallback(() => {
    const annCvs = getCanvas();
    if (!annCvs) return;
    if (streamOverlay) {
      renderEmptyCanvas();
      return;
    }
    if (!annCvs.width || !annCvs.height) {
      annCvs.width = DEFAULT_CANVAS_W;
      annCvs.height = DEFAULT_CANVAS_H;
    }
    renderEmptyCanvas();
  }, [getCanvas, renderEmptyCanvas, streamOverlay]);

  const renderFrameAndEnterAnnotating = useCallback(
    (imageSrc, loadedMsg) => {
      if (!bgImageRef.current) {
        bgImageRef.current = new Image();
      }
      const bgImage = bgImageRef.current;
      bgImage.src = imageSrc;
      bgImage.onload = () => {
        const annCvs = getCanvas();
        if (!annCvs) return;
        annCvs.width = bgImage.width;
        annCvs.height = bgImage.height;
        if (!streamOverlay) {
          renderAnnotator();
        } else {
          renderAnnotator();
        }
        setStatus(loadedMsg || '请按顺序点击 4 个点：左上、右上、右下、左下。', 'ok');
        bumpRender();
      };
    },
    [getCanvas, renderAnnotator, setStatus, bumpRender, streamOverlay],
  );

  const applyAnnotationToGrid = useCallback(
    (ann, rowsOverride, colsOverride) => {
      if (!ann || !Array.isArray(ann.boxes) || !mFwdRef.current) return;

      const rowCount = rowsOverride ?? gridRows;
      const colCount = colsOverride ?? gridCols;
      const ySamples = Array.from({ length: rowCount + 1 }, () => []);
      const xSamples = Array.from({ length: rowCount }, () =>
        Array.from({ length: colCount + 1 }, () => []),
      );

      const overrides = {};

      for (const box of ann.boxes) {
        const rowIdx = Number(box.layer) - 1;
        const colIdx = Number(box.column) - 1;
        if (rowIdx < 0 || colIdx < 0 || rowIdx >= rowCount || colIdx >= colCount) {
          continue;
        }

        const poly = Array.isArray(box.video_polygon) ? box.video_polygon : [];
        if (poly.length < 4) continue;

        const flat = poly.map((pt) => perspectiveTransform(pt, mFwdRef.current));
        const topY = (flat[0][1] + flat[1][1]) / 2;
        const bottomY = (flat[2][1] + flat[3][1]) / 2;
        const leftX = (flat[0][0] + flat[3][0]) / 2;
        const rightX = (flat[1][0] + flat[2][0]) / 2;

        ySamples[rowIdx].push(topY);
        ySamples[rowIdx + 1].push(bottomY);
        xSamples[rowIdx][colIdx].push(leftX);
        xSamples[rowIdx][colIdx + 1].push(rightX);

        const boxId = box.box_id !== undefined ? String(box.box_id) : '';
        const defaultId = String(rowIdx * colCount + colIdx + 1);
        if (boxId && boxId !== defaultId) {
          overrides[getCellKey(rowIdx, colIdx)] = boxId;
        }
      }

      const layerYs = [...layerYsRef.current];
      const layerColXs = layerColXsRef.current.map((row) => [...row]);

      for (let i = 0; i <= rowCount; i++) {
        if (ySamples[i].length) {
          layerYs[i] = averageArray(ySamples[i]);
        }
      }

      for (let i = 0; i < rowCount; i++) {
        for (let j = 0; j <= colCount; j++) {
          if (xSamples[i][j].length) {
            layerColXs[i][j] = averageArray(xSamples[i][j]);
          }
        }
      }

      layerYsRef.current = layerYs;
      layerColXsRef.current = layerColXs;
      setBoxIdOverrides(overrides);
    },
    [gridRows, gridCols],
  );

  const scaleAnnotationGeometry = useCallback(
    (fromW, fromH, toW, toH) => {
      if (!fromW || !fromH || !toW || !toH) return;
      const sx = toW / fromW;
      const sy = toH / fromH;
      if (Math.abs(sx - 1) < 1e-6 && Math.abs(sy - 1) < 1e-6) return;

      if (shelfPointsRef.current.length) {
        shelfPointsRef.current = shelfPointsRef.current.map(([x, y]) => [x * sx, y * sy]);
      }
      if (loadedAnnotationRef.current?.boxes) {
        loadedAnnotationRef.current = {
          boxes: loadedAnnotationRef.current.boxes.map((box) => ({
            ...box,
            video_polygon: (box.video_polygon || []).map(([x, y]) => [x * sx, y * sy]),
          })),
        };
      }
      if (cellPolygonsRef.current.size) {
        const next = new Map();
        for (const [key, poly] of cellPolygonsRef.current) {
          next.set(key, poly.map(([x, y]) => [x * sx, y * sy]));
        }
        cellPolygonsRef.current = next;
      }
      if (shelfPointsRef.current.length === 4) {
        refreshPerspectiveFromShelf();
        initGrid();
        if (mInvRef.current && loadedAnnotationRef.current?.boxes?.length) {
          applyAnnotationToGrid(loadedAnnotationRef.current);
          loadCellPolygonsFromBoxes(loadedAnnotationRef.current.boxes);
        }
        fillMissingCellPolygons();
      }
    },
    [
      refreshPerspectiveFromShelf,
      initGrid,
      applyAnnotationToGrid,
      loadCellPolygonsFromBoxes,
      fillMissingCellPolygons,
    ],
  );

  const mapShelfGeometryToFrame = useCallback((shelf, frameW, frameH) => {
    const annSize = annotationSizeRef.current;
    const fw = Math.max(1, Number(frameW) || 1);
    const fh = Math.max(1, Number(frameH) || 1);
    const mapPts = (pts, norm) => mapPointsToVideoFrame(pts, norm, annSize, fw, fh);

    let corners = Array.isArray(shelf.shelf_corners)
      ? shelf.shelf_corners.map((pt) => [Number(pt[0]), Number(pt[1])])
      : [];
    if (corners.length !== 4) {
      corners = createDefaultShelfCorners(fw, fh);
    } else if (annSize) {
      corners = mapPts(corners, null);
    }

    const boxes = (Array.isArray(shelf.boxes) ? shelf.boxes : []).map((box) => {
      const norm = isNormPolygonValid(box.video_polygon_norm) ? box.video_polygon_norm : null;
      const poly = Array.isArray(box.video_polygon) ? box.video_polygon : [];
      if (!poly.length || !annSize) return box;
      return {
        ...box,
        video_polygon: mapPts(poly, norm),
      };
    });

    return { ...shelf, shelf_corners: corners, boxes };
  }, []);

  const loadShelfIntoEditor = useCallback(
    (shelf, options = {}) => {
      if (!shelf) return;
      const preserveGridSize = options.preserveGridSize === true;
      const annCvs = getCanvas();
      const frameW = Number(annCvs?.width) || coordSpaceRef.current.w || DEFAULT_CANVAS_W;
      const frameH = Number(annCvs?.height) || coordSpaceRef.current.h || DEFAULT_CANVAS_H;
      const mapped = mapShelfGeometryToFrame(shelf, frameW, frameH);

      let corners = mapped.shelf_corners;
      shelfPointsRef.current = corners;
      refreshPerspectiveFromShelf();

      let shapeRows = gridRowsRef.current;
      let shapeCols = gridColsRef.current;
      if (!preserveGridSize && Array.isArray(shelf.grid_shape) && shelf.grid_shape.length === 2) {
        const rows = Number(shelf.grid_shape[0]);
        const cols = Number(shelf.grid_shape[1]);
        if (Number.isInteger(rows) && Number.isInteger(cols)) {
          shapeRows = rows;
          shapeCols = cols;
          setGridRows(rows);
          setGridCols(cols);
        }
      }

      const hasBoxes = Array.isArray(mapped.boxes) && mapped.boxes.length > 0;
      const hasLocalGrid = layerYsRef.current.length > 0;
      if (!preserveGridSize) {
        setBoxIdOverrides({});
        setSelectedCell(null);
      }
      if (mFwdRef.current && hasBoxes) {
        initGrid(shapeRows, shapeCols);
        applyAnnotationToGrid({ boxes: mapped.boxes || [] }, shapeRows, shapeCols);
        loadCellPolygonsFromBoxes(mapped.boxes || []);
        markUnmappedCellsDeleted();
      } else if (!preserveGridSize || !hasLocalGrid) {
        layerYsRef.current = [];
        layerColXsRef.current = [];
        cellPolygonsRef.current = new Map();
        deletedCellsRef.current = new Set();
      } else if (mInvRef.current) {
        fillMissingCellPolygons();
      }

      loadedAnnotationRef.current = { boxes: mapped.boxes || [] };
      if (annCvs?.width && annCvs?.height) {
        coordSpaceRef.current = { w: annCvs.width, h: annCvs.height };
      }
      setCanSave(!!mInvRef.current && hasBoxes);
      renderAnnotator();
      bumpRender();
    },
    [
      getCanvas,
      refreshPerspectiveFromShelf,
      initGrid,
      applyAnnotationToGrid,
      loadCellPolygonsFromBoxes,
      markUnmappedCellsDeleted,
      fillMissingCellPolygons,
      renderAnnotator,
      bumpRender,
      mapShelfGeometryToFrame,
    ],
  );

  const syncCanvasSize = useCallback(
    (w, h) => {
      const annCvs = getCanvas();
      if (!annCvs || !w || !h) return;
      const fw = Math.round(w);
      const fh = Math.round(h);
      const prev = coordSpaceRef.current;
      const sizeChanged = prev.w !== fw || prev.h !== fh;
      const bitmapResized = annCvs.width !== fw || annCvs.height !== fh;
      if (bitmapResized) {
        annCvs.width = fw;
        annCvs.height = fh;
      }
      coordSpaceRef.current = { w: fw, h: fh };

      if (streamOverlay && sizeChanged) {
        if (prev.w > 0 && prev.h > 0) {
          scaleAnnotationGeometry(prev.w, prev.h, fw, fh);
        } else {
          const idx = activeShelfIndexRef.current;
          const shelf = idx >= 0 ? shelvesRef.current[idx] : null;
          if (shelf) {
            loadShelfIntoEditor(shelf);
          }
        }
      } else if (!streamOverlay && prev.w > 0 && prev.h > 0 && sizeChanged) {
        scaleAnnotationGeometry(prev.w, prev.h, fw, fh);
      }
      if (bitmapResized || sizeChanged) {
        renderAnnotator();
        bumpRender();
      }
    },
    [
      getCanvas,
      renderAnnotator,
      bumpRender,
      streamOverlay,
      scaleAnnotationGeometry,
      loadShelfIntoEditor,
    ],
  );

  const loadSelectedShelf = useCallback(() => {
    const code = (selectedShelfCode || '').trim();
    if (!code) return;
    const idx = shelves.findIndex((s) => String(s.shelf_code || '') === code);
    if (idx < 0) return;
    loadShelfIntoEditor(shelves[idx]);
    activeShelfIndexRef.current = idx;
    setCameraName(code);
  }, [selectedShelfCode, shelves, loadShelfIntoEditor]);

  const getCameraUrlFromUI = useCallback(() => (cameraIp || '').trim(), [cameraIp]);

  const getCameraNameFromUI = useCallback(
    (url) => {
      const typedName = (cameraName || '').trim();
      if (typedName) return typedName;
      return cameraNameByUrlRef.current[url] || '';
    },
    [cameraName],
  );

  const getShelfCodeFromUI = useCallback(
    (url) => getCameraNameFromUI(url),
    [getCameraNameFromUI],
  );

  const resolveActiveShelfCode = useCallback(() => {
    const tabCode = String(selectedShelfCode || '').trim();
    if (embedded) {
      if (tabCode) return tabCode;
      if (activeShelfIndexRef.current >= 0) {
        return String(shelves[activeShelfIndexRef.current]?.shelf_code || '').trim();
      }
      return '';
    }
    const fromUi = String(getShelfCodeFromUI(getCameraUrlFromUI()) || '').trim();
    if (fromUi) return fromUi;
    if (activeShelfIndexRef.current >= 0) {
      return String(shelves[activeShelfIndexRef.current]?.shelf_code || '').trim();
    }
    return '';
  }, [embedded, selectedShelfCode, shelves, getShelfCodeFromUI, getCameraUrlFromUI]);

  const validateBoxIdsBeforeSave = useCallback(() => {
    const seen = new Set();
    const validPattern = /^[A-Za-z0-9_-]+$/;
    const { rows: extentRows, cols: extentCols } = getGridExtent();
    for (let i = 0; i < extentRows; i++) {
      for (let j = 0; j < extentCols; j++) {
        if (!getCellPoly(i, j)) continue;
        const idVal = String(getEffectiveBoxId(i, j) || '').trim();
        if (!idVal) {
          return { ok: false, message: '存在空的货位编号，请填写后保存' };
        }
        if (!validPattern.test(idVal)) {
          return {
            ok: false,
            message: `货位编号不合法：${idVal}（仅支持字母、数字、下划线、中划线）`,
          };
        }
        if (seen.has(idVal)) {
          return { ok: false, message: `货位编号重复：${idVal}，请保证每个货位编号唯一` };
        }
        seen.add(idVal);
      }
    }
    return { ok: true, message: '' };
  }, [getGridExtent, getCellPoly, getEffectiveBoxId]);

  const validateShelfCodeUnique = useCallback(
    (code) => {
      const nextCode = String(code || '').trim();
      if (!nextCode) return { ok: false, message: '货架名称不能为空' };
      const existing = shelves.some((s, idx) => {
        if (activeShelfIndexRef.current === idx) return false;
        return String(s.shelf_code || '').trim() === nextCode;
      });
      if (existing) {
        return { ok: false, message: `货架名称重复：${nextCode}` };
      }
      return { ok: true, message: '' };
    },
    [shelves],
  );

  const buildBoxes = useCallback(() => {
    const annCvs = getCanvas();
    const boxes = [];
    const frameW = Number(annCvs?.width || 0);
    const frameH = Number(annCvs?.height || 0);
    const { rows: extentRows, cols: extentCols } = getGridExtent();
    for (let i = 0; i < extentRows; i++) {
      for (let j = 0; j < extentCols; j++) {
        const poly = getCellPoly(i, j);
        if (!poly || poly.length < 4) continue;
        const customBoxId = getEffectiveBoxId(i, j);
        boxes.push({
          box_id: customBoxId,
          layer: i + 1,
          column: j + 1,
          video_polygon: clonePoly(poly),
          video_polygon_norm: poly.map((pt) => {
            const xNorm = frameW > 0 ? pt[0] / frameW : 0;
            const yNorm = frameH > 0 ? pt[1] / frameH : 0;
            return [xNorm, yNorm];
          }),
        });
      }
    }
    return boxes;
  }, [getCanvas, getGridExtent, getCellPoly, getEffectiveBoxId]);

  const makeShelfPayload = useCallback(
    (shelfCode) => {
      const code = String(shelfCode || '').trim();
      const existing = shelves.find((s) => String(s.shelf_code || '').trim() === code);
      return {
        shelf_code: code,
        shelf_id: String(existing?.shelf_id || '').trim(),
        shelf_name: String(existing?.shelf_name || '').trim(),
        shelf_corners: shelfPointsRef.current,
        grid_shape: [gridRows, gridCols],
        boxes: buildBoxes(),
      };
    },
    [gridRows, gridCols, buildBoxes, shelves],
  );

  const makeAnnotationPayload = useCallback(
    (shelvesOverride) => {
      const annCvs = getCanvas();
      const frameW = Number(annCvs?.width || 0);
      const frameH = Number(annCvs?.height || 0);
      const src = annotationSourceRef.current;
      return {
        annotation_size: {
          width: frameW,
          height: frameH,
        },
        source_info: {
          capture_source: src.capture_source || 'file',
          camera_url: src.camera_url || '',
          camera_name: src.camera_name || '',
        },
        shelves: shelvesOverride ?? shelves,
      };
    },
    [getCanvas, shelves],
  );

  const upsertShelfInList = useCallback((prev, payload) => {
    const code = String(payload.shelf_code || '').trim();
    if (!code) return prev;
    const idx = prev.findIndex((s) => String(s.shelf_code || '') === code);
    let next;
    if (idx >= 0) {
      next = [...prev];
      next[idx] = payload;
      activeShelfIndexRef.current = idx;
    } else {
      next = [...prev, payload];
      activeShelfIndexRef.current = next.length - 1;
    }
    setSelectedShelfCode(code);
    return next;
  }, []);

  const resetAnnotationSession = useCallback(
    (options) => {
      const opts = options || {};
      const preserveCanvas = opts.preserveCanvas === true;
      const preserveLoaded = opts.preserveLoaded === true;
      const preserveGrid = opts.preserveGrid === true;
      if (!preserveGrid) {
        shelfPointsRef.current = [];
        mFwdRef.current = null;
        mInvRef.current = null;
        layerYsRef.current = [];
        layerColXsRef.current = [];
        cellPolygonsRef.current = new Map();
        deletedCellsRef.current = new Set();
      }
      dragTargetRef.current = null;
      dragStartFlatRef.current = null;
      dragStartStateRef.current = null;
      if (!preserveGrid) {
        setBoxIdOverrides({});
      }
      if (!preserveLoaded) {
        loadedAnnotationRef.current = null;
      }
      if (!preserveCanvas) {
        const annCvs = getCanvas();
        const annCtx = getCtx();
        if (annCvs && annCtx) {
          annCtx.clearRect(0, 0, annCvs.width, annCvs.height);
          renderEmptyCanvas();
        }
      }
      setCanSave(false);
      bumpRender();
      if (!preserveGrid) {
        setStatus('已重置。请重新上传并标注。', 'warn');
      }
    },
    [getCanvas, getCtx, renderEmptyCanvas, setStatus, bumpRender],
  );

  const resetForNextShelf = useCallback(() => {
    resetAnnotationSession({ preserveCanvas: true, preserveLoaded: false, preserveGrid: false });
    loadedAnnotationRef.current = null;
    shelfPointsRef.current = [];
    mFwdRef.current = null;
    mInvRef.current = null;
    cellPolygonsRef.current = new Map();
    deletedCellsRef.current = new Set();
    activeShelfIndexRef.current = -1;
    renderAnnotator();
    bumpRender();
  }, [resetAnnotationSession, renderAnnotator, bumpRender]);

  const annotationApiPath = useCallback(() => {
    const camId = String(fixedCameraRef.current?.id || '').trim();
    if (camId) {
      return `/api/cameras/${encodeURIComponent(camId)}/annotation`;
    }
    return '/api/annotation';
  }, []);

  const applyAnnotationPayload = useCallback(
    (payload, options = {}) => {
      const silent = options.silent === true;
      if (payload?.status === 'error' || payload?.error) {
        setShelves([]);
        setSelectedShelfCode('');
        return false;
      }
      if (payload?.status !== 'success') {
        return false;
      }

      const ann = payload.data || payload;
      if (!ann || typeof ann !== 'object') {
        return false;
      }

      const annCvs = getCanvas();
      if (ann.annotation_size) {
        const aw = Number(ann.annotation_size.width) || 0;
        const ah = Number(ann.annotation_size.height) || 0;
        if (aw > 0 && ah > 0) {
          annotationSizeRef.current = { width: aw, height: ah };
          if (annCvs && !streamOverlay) {
            annCvs.width = aw;
            annCvs.height = ah;
            coordSpaceRef.current = { w: aw, h: ah };
          }
        }
      }

      if (ann.source_info) {
        annotationSourceRef.current = {
          capture_source: ann.source_info.capture_source || 'file',
          camera_url: ann.source_info.camera_url || '',
          camera_name: ann.source_info.camera_name || '',
          shelf_code: ann.source_info.shelf_code || '',
        };
        if (annotationSourceRef.current.camera_url) {
          setCameraIp(annotationSourceRef.current.camera_url);
        }
      }

      if (Array.isArray(ann.shelves)) {
        const nextShelves = ann.shelves.filter((s) => s && s.shelf_code);
        if (!nextShelves.length) {
          return false;
        }
        const prevCode = String(selectedShelfCodeRef.current || '').trim();
        const keepIdx = prevCode
          ? nextShelves.findIndex((s) => String(s.shelf_code || '').trim() === prevCode)
          : -1;
        const pickIdx = keepIdx >= 0 ? keepIdx : 0;
        const pickShelf = nextShelves[pickIdx];
        setShelves(nextShelves);
        activeShelfIndexRef.current = pickIdx;
        setSelectedShelfCode(pickShelf.shelf_code);
        loadShelfIntoEditor(pickShelf);
        setCameraName(pickShelf.shelf_code || '');
      } else if (Array.isArray(ann.boxes) && ann.boxes.length) {
        const legacyShelfCode =
          (ann.source_info && (ann.source_info.shelf_code || ann.source_info.camera_name)) ||
          'SHELF_1';
        const legacyShelf = {
          shelf_code: legacyShelfCode,
          shelf_corners: ann.shelf_corners || [],
          grid_shape: ann.grid_shape || [gridRows, gridCols],
          boxes: ann.boxes,
        };
        setShelves([legacyShelf]);
        activeShelfIndexRef.current = 0;
        setSelectedShelfCode(legacyShelfCode);
        loadShelfIntoEditor(legacyShelf);
        setCameraName(legacyShelfCode);
      } else {
        return false;
      }

      if (!silent) {
        setStatus('已加载已保存的标注，可继续编辑。', 'ok');
      }
      return true;
    },
    [getCanvas, gridRows, gridCols, loadShelfIntoEditor, setStatus, streamOverlay],
  );

  const loadAnnotationOnStartup = useCallback(
    async (options) => {
      const opts = options || {};
      const silent = opts.silent === true;
      if (embedded && !fixedCameraRef.current?.id) {
        return;
      }
      try {
        const payload = await apiGet(annotationApiPath());
        applyAnnotationPayload(payload, { silent });
      } catch {
        // no-op: 没有标注文件时不显示
      }
    },
    [embedded, annotationApiPath, applyAnnotationPayload],
  );

  const loadLastFrameOnStartup = useCallback(async () => {
    if (bgImageRef.current?.src) return;
    try {
      const payload = await apiGet('/api/last_frame');
      if (!payload || payload.error || !payload.image) {
        return;
      }
      renderFrameAndEnterAnnotating(
        `data:image/jpeg;base64,${payload.image}`,
        '已加载上次抓帧画面。',
      );
    } catch {
      // no-op: 没有缓存帧时不处理
    }
  }, [renderFrameAndEnterAnnotating]);

  const loadCameraIpList = useCallback(async () => {
    try {
      const data = await apiGet('/api/camera_ips');
      const items = Array.isArray(data.items) ? data.items : [];
      const nameMap = {};
      const list = items.map((item) => {
        nameMap[item.url] = item.name || '';
        return {
          url: item.url,
          name: item.name || '',
          label: `${item.name || item.url} (${item.url})`,
        };
      });
      cameraNameByUrlRef.current = nameMap;
      setCameraIpList(list);
    } catch {
      setStatus('加载摄像头列表失败，请确认系统已启动。', 'warn');
    }
  }, [setStatus]);

  const saveAllShelvesToServer = useCallback(async (shelvesOverride, options = {}) => {
    const silent = options.silent === true;
    const allowEmpty = options.allowEmpty === true;
    const shelvesToSave = shelvesOverride ?? shelves;
    if (!shelvesToSave.length && !allowEmpty) {
      if (!silent) alert('暂无可保存的货架标注');
      return { ok: false };
    }

    if (!silent) setStatus('正在保存标注...', 'warn');
    const payload = makeAnnotationPayload(shelvesToSave);

    try {
      const camId = String(fixedCameraRef.current?.id || '').trim();
      const saveUrl = camId
        ? `/api/cameras/${encodeURIComponent(camId)}/annotation`
        : '/api/save_annotation';
      const result = await apiPost(saveUrl, payload);
      if (result?.status === 'success') {
        if (!silent) {
          setStatus('标注已保存。', 'ok');
          await loadAnnotationOnStartup({ silent: true });
        }
        return { ok: true, nextShelves: shelvesToSave };
      }

      if (!silent) {
        setStatus(`保存失败：${formatUserError(result?.error) || '未知错误'}`, 'err');
      }
      return { ok: false };
    } catch (err) {
      if (!silent) {
        setStatus(`保存失败：${formatUserError(err.message) || '无法连接服务器'}`, 'err');
      }
      return { ok: false };
    }
  }, [shelves, makeAnnotationPayload, setStatus, loadAnnotationOnStartup]);

  const normalizeGridSize = useCallback((newRows, newCols) => {
    const rows = Number(newRows);
    const cols = Number(newCols);
    if (
      !Number.isInteger(rows) ||
      !Number.isInteger(cols) ||
      rows < 1 ||
      rows > 8 ||
      cols < 1 ||
      cols > 8
    ) {
      return null;
    }
    return { rows, cols };
  }, []);

  const setGridDimensions = useCallback(
    (newRows, newCols) => {
      const size = normalizeGridSize(newRows, newCols);
      if (!size) {
        setStatus('网格行列范围必须在 1-8。', 'err');
        return;
      }
      setGridRows(size.rows);
      setGridCols(size.cols);
      if (shelfPointsRef.current.length === 4 && mInvRef.current) {
        setStatus(`已选 ${size.rows}×${size.cols}，点击「确认生成」创建货位。`, 'ok');
      } else {
        setStatus(`已预选 ${size.rows}×${size.cols}。`, 'ok');
      }
      bumpRender();
    },
    [normalizeGridSize, setStatus, bumpRender],
  );

  const confirmGenerateGrid = useCallback(() => {
    if (shelfPointsRef.current.length < 4 || !mInvRef.current) {
      alert('区域未就绪，请稍候或切换货架后重试');
      return;
    }
    const size = normalizeGridSize(gridRows, gridCols);
    if (!size) {
      setStatus('网格行列范围必须在 1-8。', 'err');
      return;
    }
    const { rows, cols } = size;
    initGrid(rows, cols);
    cellPolygonsRef.current = new Map();
    deletedCellsRef.current = new Set();
    fillMissingCellPolygons();
    setSelectedCell(null);
    renderAnnotator();
    setCanSave(true);
    if (!embedded) {
      setStatus(`已生成 ${rows}×${cols} 个货位，点击货位编辑编号。`, 'ok');
    }
    bumpRender();
    if (embedded) {
      void autoSaveRef.current.immediate();
    }
  }, [
    gridRows,
    gridCols,
    embedded,
    normalizeGridSize,
    initGrid,
    fillMissingCellPolygons,
    renderAnnotator,
    setStatus,
    bumpRender,
  ]);

  const applyGridSelection = useCallback(
    (newRows, newCols) => {
      setGridDimensions(newRows, newCols);
    },
    [setGridDimensions],
  );

  const applyGridShape = setGridDimensions;

  const onBoxIdChange = useCallback(
    (rowIdx, colIdx, rawValue) => {
      const key = getCellKey(rowIdx, colIdx);
      const raw = String(rawValue || '').trim();
      setBoxIdOverrides((prev) => {
        const next = { ...prev };
        if (!raw) {
          delete next[key];
        } else {
          next[key] = raw;
        }
        return next;
      });
      renderAnnotator();
      setCanSave(!!mInvRef.current);
      bumpRender();
      if (embedded) autoSaveRef.current.schedule();
    },
    [renderAnnotator, bumpRender, embedded],
  );

  const selectCell = useCallback(
    (rowIdx, colIdx) => {
      setSelectedCell({ rowIdx, colIdx });
      bumpRender();
    },
    [bumpRender],
  );

  const clearSelectedCell = useCallback(() => {
    setSelectedCell(null);
    bumpRender();
  }, [bumpRender]);

  const deleteSelectedCell = useCallback(() => {
    if (!selectedCell) return;
    const { rowIdx, colIdx } = selectedCell;
    const key = getCellKey(rowIdx, colIdx);
    if (!getCellPoly(rowIdx, colIdx)) return;

    deletedCellsRef.current.add(key);
    cellPolygonsRef.current.delete(key);
    setBoxIdOverrides((prev) => {
      const next = { ...prev };
      delete next[key];
      return next;
    });
    setSelectedCell(null);
    renderAnnotator();
    setCanSave(!!mInvRef.current);
    bumpRender();
    if (embedded) void autoSaveRef.current.immediate();
  }, [selectedCell, getCellPoly, renderAnnotator, bumpRender, embedded]);

  const uploadVideo = useCallback(async () => {
    const file = videoFileRef.current?.files?.[0];
    if (!file) {
      alert('请先选择视频文件');
      return;
    }

    resetAnnotationSession({ preserveCanvas: true, preserveLoaded: true, preserveGrid: true });
    annotationSourceRef.current = {
      capture_source: 'file',
      camera_url: '',
      camera_name: '',
      shelf_code: '',
    };
    setStatus('正在上传视频并提取首帧...', 'warn');

    const fd = new FormData();
    fd.append('file', file);

    try {
      const uploadResp = await fetch('/api/upload_video', {
        method: 'POST',
        credentials: 'include',
        body: fd,
      });
      if (!uploadResp.ok) {
        setStatus('上传失败，请稍后重试。', 'err');
        return;
      }

      const data = await apiGet('/api/get_first_frame');
      if (data?.error || !data?.image) {
        setStatus(`提取首帧失败：${formatUserError(data?.error) || '未知错误'}`, 'err');
        return;
      }

      renderFrameAndEnterAnnotating(`data:image/jpeg;base64,${data.image}`);
    } catch (err) {
      setStatus(`请求失败：${formatUserError(err.message) || '无法连接服务器'}`, 'err');
    }
  }, [resetAnnotationSession, setStatus, renderFrameAndEnterAnnotating]);

  const captureFrame = useCallback(async () => {
    const url = getCameraUrlFromUI();
    if (!url) {
      alert('请选择或输入摄像头视频流地址');
      return;
    }

    resetAnnotationSession({ preserveCanvas: true, preserveLoaded: true, preserveGrid: true });
    setStatus('正在通过摄像头抓取一帧...', 'warn');

    try {
      let imagePayload = '';
      if (typeof getBrowserCapture === 'function') {
        try {
          imagePayload = getBrowserCapture();
        } catch (capErr) {
          setStatus(
            `摄像头抓帧失败：${formatUserError(capErr.message) || '请先在实时监控页等待画面加载'}`,
            'err',
          );
          return;
        }
      }
      if (!imagePayload) {
        setStatus('请先在「实时监控」等待画面加载后再抓帧', 'err');
        return;
      }
      const data = await apiPost('/api/get_camera_frame', { url, image: imagePayload });
      if (data?.error || !data?.image) {
        setStatus(`摄像头抓帧失败：${formatUserError(data?.error) || '未知错误'}`, 'err');
        return;
      }

      annotationSourceRef.current = {
        capture_source: 'camera',
        camera_url: url,
        camera_name: getCameraNameFromUI(url) || '',
        shelf_code: '',
      };

      renderFrameAndEnterAnnotating(
        `data:image/jpeg;base64,${data.image}`,
        '摄像头帧已加载。请按顺序点击 4 个点：左上、右上、右下、左下。',
      );
    } catch (err) {
      setStatus(`摄像头抓帧失败：${formatUserError(err.message) || '无法连接服务器'}`, 'err');
    }
  }, [
    getBrowserCapture,
    getCameraUrlFromUI,
    resetAnnotationSession,
    setStatus,
    getCameraNameFromUI,
    renderFrameAndEnterAnnotating,
  ]);

  const addCameraIp = useCallback(async () => {
    const url = (cameraIp || '').trim();
    const name = (cameraName || '').trim();

    if (!url) {
      alert('请先输入摄像头视频流地址');
      return;
    }

    try {
      const data = await apiPost('/api/camera_ips', { url, name });
      if (data?.error) {
        setStatus(`保存失败：${formatUserError(data.error) || '未知错误'}`, 'err');
        return;
      }

      await loadCameraIpList();
      setCameraIp(url);
      setStatus('摄像头已保存。', 'ok');
    } catch (err) {
      setStatus(`保存失败：${formatUserError(err.message) || '无法连接服务器'}`, 'err');
    }
  }, [cameraIp, cameraName, loadCameraIpList, setStatus]);

  const deleteCameraIp = useCallback(async () => {
    const url = (cameraIp || '').trim();
    if (!url) {
      alert('请先选择要删除的摄像头');
      return;
    }

    if (!confirm(`确认删除该摄像头吗？\n${url}`)) {
      return;
    }

    try {
      const data = await apiDelete('/api/camera_ips', { url });
      if (data?.error) {
        setStatus(`删除失败：${formatUserError(data.error) || '未知错误'}`, 'err');
        return;
      }

      await loadCameraIpList();
      setCameraIp('');
      setCameraName('');
      setStatus('已删除所选摄像头。', 'ok');
    } catch (err) {
      setStatus(`删除失败：${formatUserError(err.message) || '无法连接服务器'}`, 'err');
    }
  }, [cameraIp, loadCameraIpList, setStatus]);

  const finishAnnotation = useCallback(() => {
    let nextShelves = shelves;
    if (shelfPointsRef.current.length >= 4 && mInvRef.current) {
      const shelfCode = resolveActiveShelfCode();
      if (!shelfCode) {
        alert(embedded ? '请先选择或创建货架' : '请先填写货架名称（必填）');
        return;
      }

      const shelfCheck = validateShelfCodeUnique(shelfCode);
      if (!shelfCheck.ok) {
        alert(shelfCheck.message);
        return;
      }

      const idCheck = validateBoxIdsBeforeSave();
      if (!idCheck.ok) {
        alert(idCheck.message);
        return;
      }

      const shelfPayload = makeShelfPayload(shelfCode);
      nextShelves = upsertShelfInList(shelves, shelfPayload);
      setShelves(nextShelves);
    }

    void saveAllShelvesToServer(nextShelves);
  }, [
    resolveActiveShelfCode,
    embedded,
    validateShelfCodeUnique,
    validateBoxIdsBeforeSave,
    makeShelfPayload,
    upsertShelfInList,
    shelves,
    saveAllShelvesToServer,
  ]);

  const saveAnnotation = useCallback(async (options = {}) => {
    const keepState = options.keepState === true;
    const silent = options.silent === true;
    if (shelfPointsRef.current.length < 4 || !mInvRef.current) {
      if (!silent) alert('请先完成四点标注');
      return { ok: false };
    }

    const shelfCode = resolveActiveShelfCode();
    if (!shelfCode) {
      if (!silent) alert(embedded ? '请先选择或创建货架' : '请先填写货架名称（必填）');
      return { ok: false };
    }

    const shelfCheck = validateShelfCodeUnique(shelfCode);
    if (!shelfCheck.ok) {
      if (!silent) alert(shelfCheck.message);
      return { ok: false };
    }

    const idCheck = validateBoxIdsBeforeSave();
    if (!idCheck.ok) {
      if (!silent) alert(idCheck.message);
      return { ok: false };
    }

    const shelfPayload = makeShelfPayload(shelfCode);
    const nextShelves = upsertShelfInList(shelves, shelfPayload);
    setShelves(nextShelves);

    const result = await saveAllShelvesToServer(nextShelves, { silent });
    if (result.ok && !keepState) {
      resetForNextShelf();
    }
    return result.ok ? { ok: true, nextShelves } : { ok: false };
  }, [
    shelves,
    resolveActiveShelfCode,
    embedded,
    validateShelfCodeUnique,
    validateBoxIdsBeforeSave,
    makeShelfPayload,
    upsertShelfInList,
    saveAllShelvesToServer,
    resetForNextShelf,
  ]);

  const resetAnnotation = useCallback(() => {
    resetAnnotationSession();
    renderAnnotator();
  }, [resetAnnotationSession, renderAnnotator]);

  const persistCurrentShelfIfReady = useCallback(
    async (options = {}) => {
      const silent = options.silent === true;
      if (shelfPointsRef.current.length < 4 || !mInvRef.current) {
        return { ok: true, skipped: true, nextShelves: shelves };
      }
      return saveAnnotation({ keepState: true, silent });
    },
    [saveAnnotation, shelves],
  );

  const saveAllAnnotations = useCallback(
    async (options = {}) => {
      const silent = options.silent === true;
      const persisted = await persistCurrentShelfIfReady({ silent });
      if (!persisted.ok) return { ok: false };
      const list = persisted.nextShelves ?? shelves;
      if (!list.length) {
        if (!silent) alert('暂无可保存的货架标注');
        return { ok: false };
      }
      return saveAllShelvesToServer(list, { silent });
    },
    [persistCurrentShelfIfReady, shelves, saveAllShelvesToServer],
  );

  const autoSaveAnnotations = useCallback(async () => {
    if (!embedded) return { ok: true };
    if (!resolveActiveShelfCode() && !shelves.length) return { ok: true, skipped: true };
    return saveAllAnnotations({ silent: true });
  }, [embedded, resolveActiveShelfCode, shelves.length, saveAllAnnotations]);

  const scheduleAutoSave = useCallback(() => {
    if (!embedded) return;
    if (autoSaveTimerRef.current) clearTimeout(autoSaveTimerRef.current);
    autoSaveTimerRef.current = setTimeout(() => {
      autoSaveTimerRef.current = null;
      void autoSaveAnnotations();
    }, 700);
  }, [embedded, autoSaveAnnotations]);

  /** 若存在挂起的防抖保存则立即执行并 await 完成，无挂起时直接返回 */
  const flushAutoSave = useCallback(async () => {
    if (!embedded) return { ok: true, skipped: true };
    if (autoSaveTimerRef.current) {
      clearTimeout(autoSaveTimerRef.current);
      autoSaveTimerRef.current = null;
      return autoSaveAnnotations();
    }
    return { ok: true, skipped: true };
  }, [embedded, autoSaveAnnotations]);

  useEffect(() => {
    autoSaveRef.current = {
      immediate: autoSaveAnnotations,
      schedule: scheduleAutoSave,
    };
    return () => {
      if (autoSaveTimerRef.current) clearTimeout(autoSaveTimerRef.current);
    };
  }, [autoSaveAnnotations, scheduleAutoSave]);

  /** 切换 Tab 前把当前画布状态写入内存货架列表（不阻塞在远端校验/保存） */
  const flushCurrentShelfToMemory = useCallback(() => {
    if (shelfPointsRef.current.length < 4 || !mInvRef.current) {
      return shelvesRef.current;
    }
    const shelfCode = resolveActiveShelfCode();
    if (!shelfCode) return shelvesRef.current;
    const payload = makeShelfPayload(shelfCode);
    const merged = upsertShelfInList(shelvesRef.current, payload);
    shelvesRef.current = merged;
    setShelves(merged);
    return merged;
  }, [resolveActiveShelfCode, makeShelfPayload, upsertShelfInList]);

  const switchShelfTab = useCallback(
    (code) => {
      const nextCode = String(code || '').trim();
      if (!nextCode || nextCode === selectedShelfCode) return;

      const list = flushCurrentShelfToMemory();
      const idx = list.findIndex((s) => String(s.shelf_code || '').trim() === nextCode);
      activeShelfIndexRef.current = idx;
      setSelectedShelfCode(nextCode);
      setCameraName(nextCode);
      if (idx >= 0) {
        loadShelfIntoEditor(list[idx]);
      } else {
        renderAnnotator();
        bumpRender();
      }
      void saveAllShelvesToServer(list, { silent: true });
    },
    [
      selectedShelfCode,
      flushCurrentShelfToMemory,
      loadShelfIntoEditor,
      renderAnnotator,
      bumpRender,
      saveAllShelvesToServer,
    ],
  );

  const deleteSelectedShelf = useCallback(
    async (codeOverride) => {
      const code = String(codeOverride || selectedShelfCode || '').trim();
      if (!code) {
        alert('请先选择要删除的货架');
        return { ok: false };
      }

      const target = shelves.find((s) => String(s.shelf_code || '').trim() === code);
      const label =
        String(target?.shelf_name || '').trim() || code;
      if (
        !window.confirm(
          `确定删除货架「${label}」？\n将同时删除其区域与货位标注，且不可恢复。`,
        )
      ) {
        return { ok: false, cancelled: true };
      }

      const removedIdx = shelves.findIndex((s) => String(s.shelf_code || '').trim() === code);
      const nextShelves = shelves.filter((s) => String(s.shelf_code || '').trim() !== code);
      setShelves(nextShelves);
      setSelectedCell(null);

      if (!nextShelves.length) {
        activeShelfIndexRef.current = -1;
        setSelectedShelfCode('');
        shelfPointsRef.current = [];
        mFwdRef.current = null;
        mInvRef.current = null;
        layerYsRef.current = [];
        layerColXsRef.current = [];
        cellPolygonsRef.current = new Map();
        deletedCellsRef.current = new Set();
        setBoxIdOverrides({});
        setCanSave(false);
        renderAnnotator();
        bumpRender();
      } else {
        let nextIdx = removedIdx >= 0 ? removedIdx : 0;
        if (nextIdx >= nextShelves.length) nextIdx = nextShelves.length - 1;
        const nextShelf = nextShelves[nextIdx];
        activeShelfIndexRef.current = nextIdx;
        setSelectedShelfCode(nextShelf.shelf_code || '');
        setCameraName(nextShelf.shelf_code || '');
        loadShelfIntoEditor(nextShelf);
      }

      const saveResult = await saveAllShelvesToServer(nextShelves, {
        silent: embedded,
        allowEmpty: true,
      });
      if (!saveResult.ok) {
        if (!embedded) setStatus('删除失败，请稍后重试。', 'err');
        return { ok: false };
      }
      return { ok: true };
    },
    [
      selectedShelfCode,
      shelves,
      embedded,
      loadShelfIntoEditor,
      saveAllShelvesToServer,
      renderAnnotator,
      bumpRender,
      setStatus,
    ],
  );

  const createShelfFromDrawer = useCallback(
    async ({ shelf_id, shelf_code, shelf_name }) => {
      const code = String(shelf_code || '').trim();
      const name = String(shelf_name || '').trim();
      const id = String(shelf_id || '').trim();
      if (!code) {
        alert('请填写货架编码');
        return { ok: false };
      }
      if (shelves.some((s) => String(s.shelf_code || '').trim() === code)) {
        alert(`货架编码重复：${code}`);
        return { ok: false };
      }

      const persisted = await persistCurrentShelfIfReady();
      if (!persisted.ok) return { ok: false };

      const annCvs = getCanvas();
      const frameW = Number(annCvs?.width) || coordSpaceRef.current.w || DEFAULT_CANVAS_W;
      const frameH = Number(annCvs?.height) || coordSpaceRef.current.h || DEFAULT_CANVAS_H;
      const baseList = persisted.nextShelves ?? shelves;

      const stub = {
        shelf_code: code,
        shelf_id: id,
        shelf_name: name,
        shelf_corners: createDefaultShelfCorners(frameW, frameH),
        grid_shape: [gridRows, gridCols],
        boxes: [],
      };
      const nextShelves = [...baseList, stub];
      setShelves(nextShelves);
      activeShelfIndexRef.current = nextShelves.length - 1;
      setSelectedShelfCode(code);
      setCameraName(code);
      loadShelfIntoEditor(stub);

      const saveResult = await saveAllShelvesToServer(nextShelves);
      if (!saveResult.ok) {
        setStatus('货架已创建，但自动保存失败，请稍后重试。', 'err');
        return { ok: false };
      }
      if (!embedded) {
        setStatus(`已创建并保存货架「${name || code}」。`, 'ok');
      }
      return { ok: true };
    },
    [
      shelves,
      gridRows,
      gridCols,
      getCanvas,
      persistCurrentShelfIfReady,
      loadShelfIntoEditor,
      saveAllShelvesToServer,
      embedded,
      setStatus,
    ],
  );

  const shelfSelectOptions = useMemo(() => {
    const opts = [
      {
        value: '',
        label: shelves.length ? '选择货架' : '暂无货架',
      },
    ];
    for (const shelf of shelves) {
      opts.push({
        value: shelf.shelf_code || '',
        label: shelf.shelf_code || '未命名货架',
      });
    }
    return opts;
  }, [shelves]);

  const boxEditorCells = useMemo(() => {
    void renderTick;
    const M_inv = mInvRef.current;
    const shelfPoints = shelfPointsRef.current;
    if (!M_inv || shelfPoints.length < 4) {
      return [];
    }

    const ys = layerYsRef.current;
    const colXs = layerColXsRef.current;
    let extentRows = gridRows;
    let extentCols = gridCols;
    if (ys.length > 1) extentRows = Math.max(extentRows, ys.length - 1);
    for (const row of colXs) {
      if (row?.length > 1) extentCols = Math.max(extentCols, row.length - 1);
    }

    const cells = [];
    for (let i = 0; i < extentRows; i++) {
      for (let j = 0; j < extentCols; j++) {
        const defaultId = getDefaultBoxId(i, j);
        cells.push({
          key: getCellKey(i, j),
          row: i + 1,
          col: j + 1,
          label: `L${i + 1} C${j + 1}`,
          value: String(getEffectiveBoxId(i, j)),
          defaultId,
          rowIdx: i,
          colIdx: j,
        });
      }
    }
    return cells;
  }, [renderTick, gridRows, gridCols, getDefaultBoxId, getEffectiveBoxId, boxIdOverrides]);

  const selectedBoxCell = useMemo(() => {
    if (!selectedCell) return null;
    return (
      boxEditorCells.find(
        (c) => c.rowIdx === selectedCell.rowIdx && c.colIdx === selectedCell.colIdx,
      ) || null
    );
  }, [boxEditorCells, selectedCell]);

  useEffect(() => {
    if (!fixedCamera?.url) return;
    setCameraIp(fixedCamera.url);
    if (!embedded) {
      setCameraName(fixedCamera.name || '');
    }
    annotationSourceRef.current = {
      ...annotationSourceRef.current,
      capture_source: 'camera',
      camera_url: fixedCamera.url,
      camera_name: fixedCamera.name || fixedCamera.id || '',
    };
  }, [fixedCamera?.url, fixedCamera?.name, fixedCamera?.id, embedded]);

  useEffect(() => {
    if (window.location.protocol === 'file:') {
      setStatus('请通过浏览器打开系统主页后使用本功能。', 'err');
    }
    bgImageRef.current = new Image();
    initCanvasPlaceholder();
    if (!embedded) {
      loadCameraIpList();
      loadAnnotationOnStartup();
      if (!streamOverlay) {
        loadLastFrameOnStartup();
      }
    }
  }, []);

  useEffect(() => {
    if (!embedded || !fixedCamera?.id || streamOverlay) return undefined;
    const camKey = String(fixedCamera.id);
    if (annotationBootstrappedRef.current === camKey) return undefined;
    annotationBootstrappedRef.current = camKey;
    loadAnnotationOnStartup({ silent: true });
    return undefined;
  }, [embedded, fixedCamera?.id, streamOverlay, loadAnnotationOnStartup]);

  const loadSelectedShelfRef = useRef(loadSelectedShelf);
  loadSelectedShelfRef.current = loadSelectedShelf;

  useEffect(() => {
    if (!canvasActive) return undefined;
    const run = () => loadSelectedShelfRef.current();
    run();
    const raf = requestAnimationFrame(run);
    return () => cancelAnimationFrame(raf);
  }, [selectedShelfCode, canvasActive]);

  useEffect(() => {
    renderAnnotator();
  }, [renderTick, renderAnnotator, boxIdOverrides, gridRows, gridCols, selectedCell]);

  useLayoutEffect(() => {
    if (!canvasActive) return undefined;

    const bindPointerHandlers = () => {
      const annCvs = getCanvas();
      if (!annCvs) return null;

    const onDown = (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      const rect = annCvs.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      const bitmapW = annCvs.width > 0 ? annCvs.width : coordSpaceRef.current.w || rect.width;
      const bitmapH = annCvs.height > 0 ? annCvs.height : coordSpaceRef.current.h || rect.height;
      const scaleX = bitmapW / rect.width;
      const scaleY = bitmapH / rect.height;
      const x = (e.clientX - rect.left) * scaleX;
      const y = (e.clientY - rect.top) * scaleY;

      const shelfPoints = shelfPointsRef.current;
      const hitPx = streamOverlay ? 18 : 12;

      if (shelfPoints.length === 4) {
        for (let i = 0; i < shelfPoints.length; i++) {
          if (getPointDist([x, y], shelfPoints[i]) < hitPx) {
            dragTargetRef.current = { type: 'shelf-corner', i };
            return;
          }
        }
      }

      const { rows: extentRows, cols: extentCols } = getGridExtent();
      let best = null;
      let bestDist = hitPx;
      for (let i = 0; i < extentRows; i++) {
        for (let j = 0; j < extentCols; j++) {
          const poly = getCellPoly(i, j);
          if (!poly) continue;
          for (let ci = 0; ci < poly.length; ci++) {
            const d = getPointDist([x, y], poly[ci]);
            if (d < bestDist) {
              bestDist = d;
              best = { type: 'cell-corner', row: i, col: j, ci };
            }
          }
        }
      }
      if (best) {
        dragTargetRef.current = best;
        return;
      }

      for (let i = 0; i < extentRows; i++) {
        for (let j = 0; j < extentCols; j++) {
          const poly = getCellPoly(i, j);
          if (!poly || poly.length < 4) continue;
          if (pointInPolygon([x, y], poly)) {
            selectCell(i, j);
            renderAnnotator();
            return;
          }
        }
      }

      clearSelectedCell();
      renderAnnotator();
    };

    const onMove = (e) => {
      if (!dragTargetRef.current) return;

      const rect = annCvs.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      const bitmapW = annCvs.width > 0 ? annCvs.width : coordSpaceRef.current.w || rect.width;
      const bitmapH = annCvs.height > 0 ? annCvs.height : coordSpaceRef.current.h || rect.height;
      const scaleX = bitmapW / rect.width;
      const scaleY = bitmapH / rect.height;
      const canvasPt = [(e.clientX - rect.left) * scaleX, (e.clientY - rect.top) * scaleY];
      const dragTarget = dragTargetRef.current;

      if (dragTarget.type === 'shelf-corner') {
        shelfPointsRef.current[dragTarget.i] = [canvasPt[0], canvasPt[1]];
        refreshPerspectiveFromShelf();
        renderAnnotator();
        setCanSave(!!mInvRef.current);
        return;
      }

      if (dragTarget.type === 'cell-corner') {
        const key = getCellKey(dragTarget.row, dragTarget.col);
        const poly = cellPolygonsRef.current.get(key) || getCellPoly(dragTarget.row, dragTarget.col);
        if (!poly) return;
        const next = clonePoly(poly);
        next[dragTarget.ci] = [canvasPt[0], canvasPt[1]];
        cellPolygonsRef.current.set(key, next);
        renderAnnotator();
        setCanSave(!!mInvRef.current);
        return;
      }
    };

    const onUp = () => {
      const hadDrag = dragTargetRef.current !== null;
      dragTargetRef.current = null;
      if (hadDrag && embedded) autoSaveRef.current.schedule();
    };

      annCvs.addEventListener('mousedown', onDown);
      annCvs.addEventListener('mousemove', onMove);
      annCvs.addEventListener('mouseup', onUp);

      return () => {
        annCvs.removeEventListener('mousedown', onDown);
        annCvs.removeEventListener('mousemove', onMove);
        annCvs.removeEventListener('mouseup', onUp);
      };
    };

    let cleanup = bindPointerHandlers();
    let rafId = 0;
    if (!cleanup) {
      rafId = requestAnimationFrame(() => {
        cleanup = bindPointerHandlers();
      });
    }

    return () => {
      if (rafId) cancelAnimationFrame(rafId);
      cleanup?.();
    };
  }, [
    canvasActive,
    getCanvas,
    getGridExtent,
    getCellPoly,
    renderAnnotator,
    selectCell,
    clearSelectedCell,
    refreshPerspectiveFromShelf,
    embedded,
  ]);

  const shelfCornersReady = useMemo(() => {
    void renderTick;
    return shelfPointsRef.current.length === 4 && !!mInvRef.current;
  }, [renderTick]);

  const gridGenerated = useMemo(() => {
    void renderTick;
    return layerYsRef.current.length > 0;
  }, [renderTick]);

  const boxEditorExtent = useMemo(() => {
    void renderTick;
    const ys = layerYsRef.current;
    const colXs = layerColXsRef.current;
    let extentRows = gridRows;
    let extentCols = gridCols;
    if (ys.length > 1) extentRows = Math.max(extentRows, ys.length - 1);
    for (const row of colXs) {
      if (row?.length > 1) extentCols = Math.max(extentCols, row.length - 1);
    }
    return { rows: extentRows, cols: extentCols };
  }, [renderTick, gridRows, gridCols]);

  return {
    statusHtml,
    statusClass,
    gridRows,
    gridCols,
    setGridRows,
    setGridCols,
    applyGridShape,
    applyGridSelection,
    setGridDimensions,
    confirmGenerateGrid,
    shelfCornersReady,
    gridGenerated,
    boxEditorRows: boxEditorExtent.rows,
    boxEditorCols: boxEditorExtent.cols,
    cameraIpList,
    cameraIp,
    setCameraIp,
    cameraName,
    setCameraName,
    shelves,
    shelfSelectOptions,
    selectedShelfCode,
    setSelectedShelfCode,
    switchShelfTab,
    deleteSelectedShelf,
    createShelfFromDrawer,
    boxEditorCells,
    selectedBoxCell,
    selectCell,
    clearSelectedCell,
    deleteSelectedCell,
    canSave,
    onBoxIdChange,
    uploadVideo,
    captureFrame,
    addCameraIp,
    deleteCameraIp,
    saveAnnotation,
    saveAllAnnotations,
    flushAutoSave,
    persistCurrentShelfIfReady,
    finishAnnotation,
    resetAnnotation,
    syncCanvasSize,
    applyAnnotationPayload,
    videoFileRef,
  };
}
