import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { apiGet } from '../api/client';
import { formatUserError } from '../lib/userFacingText';
import './MatrixPage.css';

const POLL_MS = 5000;

const STATE_META = {
  empty: { label: '空', className: 'cell-empty' },
  configured: { label: '待命', className: 'cell-configured' },
  monitoring: { label: '监测', className: 'cell-monitoring' },
  hit: { label: '碰撞', className: 'cell-hit' },
  alarm: { label: '告警', className: 'cell-alarm' },
};

const INFER_LABEL = {
  stopped: '未检测',
  running: '检测中',
  starting: '启动中',
  error: '异常',
  paused: '暂停',
};

function ShelfMatrix({ shelf, cameraId }) {
  const [rows, cols] = shelf.grid_shape?.length >= 2 ? shelf.grid_shape : [0, 0];
  const placed = (shelf.cells || []).filter((c) => !c.unplaced);
  const unplaced = (shelf.cells || []).filter((c) => c.unplaced);

  if (!rows || !cols) {
    return (
      <div className="matrix-shelf matrix-shelf--list">
        <div className="matrix-shelf-head">
          <span className="matrix-shelf-code">{shelf.shelf_code}</span>
          {shelf.shelf_name ? <span className="matrix-shelf-name">{shelf.shelf_name}</span> : null}
          <span className="matrix-shelf-meta">{shelf.box_count} 货位</span>
        </div>
        <div className="matrix-chip-row">
          {(shelf.cells || []).map((cell) => (
            <MatrixCell key={cell.roi_key || cell.box_id} cell={cell} cameraId={cameraId} compact />
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="matrix-shelf">
      <div className="matrix-shelf-head">
        <span className="matrix-shelf-code">{shelf.shelf_code}</span>
        {shelf.shelf_name ? <span className="matrix-shelf-name">{shelf.shelf_name}</span> : null}
        <span className="matrix-shelf-meta">
          {rows}×{cols}
        </span>
      </div>
      <div
        className="matrix-grid"
        style={{ gridTemplateColumns: `repeat(${cols}, minmax(18px, 1fr))` }}
        role="grid"
        aria-label={`货架 ${shelf.shelf_code}`}
      >
        {placed.map((cell) => (
          <MatrixCell
            key={`${cell.layer}-${cell.column}-${cell.roi_key}`}
            cell={cell}
            cameraId={cameraId}
          />
        ))}
      </div>
      {unplaced.length ? (
        <div className="matrix-unplaced">
          <span className="matrix-unplaced-label">未编网格</span>
          <div className="matrix-chip-row">
            {unplaced.map((cell) => (
              <MatrixCell key={cell.roi_key || cell.box_id} cell={cell} cameraId={cameraId} compact />
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function MatrixCell({ cell, cameraId, compact = false }) {
  const meta = STATE_META[cell.state] || STATE_META.configured;
  const hasBox = cell.state !== 'empty' && (cell.box_id || cell.roi_key);
  const title = hasBox
    ? `${cell.roi_key || cell.box_id} · ${meta.label}`
    : `L${cell.layer} C${cell.column}`;

  const code = cell.box_id || cell.roi_key || null;
  const inner = code ? <span className="matrix-cell-id">{code}</span> : null;

  if (!hasBox) {
    return (
      <div
        className={`matrix-cell ${meta.className}${compact ? ' matrix-cell--compact' : ''}`}
        title={title}
        role="gridcell"
        aria-label={meta.label}
      />
    );
  }

  return (
    <Link
      to={`/monitor?camera=${encodeURIComponent(cameraId)}`}
      className={`matrix-cell ${meta.className}${compact ? ' matrix-cell--compact' : ''}`}
      title={title}
      role="gridcell"
      aria-label={title}
    >
      {inner}
    </Link>
  );
}

function CameraBlock({ cam }) {
  const infer = cam.inference?.status || 'stopped';

  return (
    <section className="matrix-camera" id={`camera-${cam.id}`}>
      <header className="matrix-camera-head">
        <div className="matrix-camera-title">
          <Link to={`/monitor?camera=${encodeURIComponent(cam.id)}`} className="matrix-camera-link">
            {cam.name || cam.id}
          </Link>
          <code className="matrix-camera-id">{cam.id}</code>
        </div>
        <div className="matrix-camera-badges">
          <span className={`matrix-badge st-${cam.online ? 'online' : 'offline'}`}>
            {cam.online ? '在线' : '离线'}
          </span>
          <span className={`matrix-badge infer-${infer}`}>{INFER_LABEL[infer] || infer}</span>
          {cam.box_count > 0 ? (
            <span className="matrix-badge">
              {cam.box_count} 货位
            </span>
          ) : (
            <span className="matrix-badge warn">无标注</span>
          )}
        </div>
      </header>

      {!cam.shelves?.length ? (
        <p className="matrix-empty-shelf">暂无货架网格，请先在监控页完成标注。</p>
      ) : (
        <div className="matrix-shelves">
          {cam.shelves.map((shelf) => (
            <ShelfMatrix key={shelf.shelf_code} shelf={shelf} cameraId={cam.id} />
          ))}
        </div>
      )}
    </section>
  );
}

export default function MatrixPage() {
  const [data, setData] = useState(null);
  const [err, setErr] = useState('');
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const res = await apiGet('/api/matrix/overview');
      if (res.error) {
        setErr(formatUserError(res.error));
        return;
      }
      setData(res);
      setErr('');
    } catch (e) {
      setErr(formatUserError(e.message) || '加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [load]);

  const updatedLabel = data?.updated_at
    ? new Date(data.updated_at * 1000).toLocaleTimeString()
    : '—';

  return (
    <div className="page matrix-page">
      <header className="matrix-toolbar">
        <div>
          <h1>事件矩阵</h1>
          <p className="matrix-sub">
            按摄像头汇总全部货架区域，实时显示碰撞与告警（约 {POLL_MS / 1000}s 刷新）
          </p>
        </div>
        <div className="matrix-toolbar-right">
          <span className="matrix-updated">更新 {updatedLabel}</span>
          <button type="button" className="matrix-refresh" onClick={load} disabled={loading}>
            刷新
          </button>
        </div>
      </header>

      <div className="matrix-legend" aria-label="图例">
        {Object.entries(STATE_META).map(([key, meta]) => (
          <span key={key} className={`matrix-legend-item ${meta.className}`}>
            {meta.label}
          </span>
        ))}
      </div>

      {err ? <p className="matrix-msg err">{err}</p> : null}
      {loading && !data ? <p className="matrix-msg">加载中…</p> : null}

      <div className="matrix-cameras">
        {(data?.cameras || []).map((cam) => (
          <CameraBlock key={cam.id} cam={cam} />
        ))}
      </div>

      {data && !data.cameras?.length ? <p className="matrix-msg">暂无摄像头</p> : null}
    </div>
  );
}
