import { useCallback, useEffect, useMemo, useState } from 'react';
import { apiGet, apiPatch } from '../api/client';
import FieldHint from './FieldHint';
import { COLLISION_SETTINGS_FIELDS } from '../lib/cameraSettings';
import { formatUserError } from '../lib/userFacingText';
import './CollisionSettingsPanel.css';

const SPATIAL_KEYS = new Set([
  'inference.collision.wrist_conf',
  'inference.collision.elbow_conf',
  'inference.collision.forearm_extend_ratio',
  'inference.collision.boundary_margin_ratio',
  'inference.collision.boundary_margin_min_px',
]);

const GATING_KEYS = new Set([
  'inference.collision.min_consecutive_frames',
  'inference.collision.window_frames',
  'inference.collision.cooldown_frames',
]);

const TRACK_KEYS = new Set([
  'inference.collision.track_max_match_dist',
  'inference.collision.track_stale_sec',
]);

function num(settings, key, fallback) {
  const v = settings[key];
  return v === undefined || v === null || v === '' ? fallback : Number(v);
}

function pointHitsRect(px, py, rx, ry, rw, rh, margin) {
  return (
    px >= rx - margin &&
    px <= rx + rw + margin &&
    py >= ry - margin &&
    py <= ry + rh + margin
  );
}

function CollisionSpatialDiagram({ settings }) {
  const wristConf = num(settings, 'inference.collision.wrist_conf', 0.45);
  const elbowConf = num(settings, 'inference.collision.elbow_conf', 0.4);
  const extendRatio = num(settings, 'inference.collision.forearm_extend_ratio', 0.2);
  const marginRatio = num(settings, 'inference.collision.boundary_margin_ratio', 0.04);
  const marginMinPx = num(settings, 'inference.collision.boundary_margin_min_px', 3);

  const shoulderW = 72;
  const margin = Math.max(marginMinPx, marginRatio * shoulderW);

  const elbow = { x: 118, y: 168 };
  const wrist = { x: 178, y: 128 };
  const extend = {
    x: wrist.x + extendRatio * (wrist.x - elbow.x),
    y: wrist.y + extendRatio * (wrist.y - elbow.y),
  };

  const cells = [
    { id: '7', x: 58, y: 72, w: 96, h: 68, label: '货位 7' },
    { id: '8', x: 166, y: 72, w: 96, h: 68, label: '货位 8' },
    { id: '3', x: 58, y: 152, w: 96, h: 68, label: '货位 3' },
    { id: '4', x: 166, y: 152, w: 96, h: 68, label: '货位 4' },
  ];

  const handPoints = [];
  if (wristConf >= 0.1) handPoints.push({ ...wrist, kind: 'wrist', active: true });
  if (elbowConf >= 0.1 && extendRatio > 0) {
    handPoints.push({ ...extend, kind: 'extend', active: true });
  }

  const hitIds = new Set();
  for (const cell of cells) {
    for (const pt of handPoints) {
      if (pt.active && pointHitsRect(pt.x, pt.y, cell.x, cell.y, cell.w, cell.h, margin)) {
        hitIds.add(cell.id);
      }
    }
  }

  return (
    <div className="collision-diagram collision-diagram--spatial">
      <div className="collision-diagram-head">
        <strong>命中范围示意图</strong>
        <span>拖动下方滑块，观察软边界与前臂外推如何扩大命中区域</span>
      </div>
      <svg viewBox="0 0 320 240" className="collision-diagram-svg" aria-label="碰撞命中范围示意图">
        <rect x="0" y="0" width="320" height="240" fill="#101820" rx="8" />
        {cells.map((cell) => {
          const hit = hitIds.has(cell.id);
          return (
            <g key={cell.id}>
              <rect
                x={cell.x - margin}
                y={cell.y - margin}
                width={cell.w + margin * 2}
                height={cell.h + margin * 2}
                fill={hit ? 'rgba(255, 170, 60, 0.12)' : 'rgba(90, 140, 180, 0.06)'}
                stroke={hit ? 'rgba(255, 170, 60, 0.55)' : 'rgba(90, 140, 180, 0.25)'}
                strokeWidth="1"
                strokeDasharray={hit ? 'none' : '4 3'}
              />
              <rect
                x={cell.x}
                y={cell.y}
                width={cell.w}
                height={cell.h}
                fill={hit ? 'rgba(255, 120, 40, 0.35)' : 'rgba(40, 70, 95, 0.55)'}
                stroke={hit ? '#ffb347' : '#5a8cb4'}
                strokeWidth="1.5"
              />
              <text x={cell.x + cell.w / 2} y={cell.y + cell.h / 2 + 4} textAnchor="middle" className="collision-diagram-label">
                {cell.label}
              </text>
            </g>
          );
        })}
        <line x1={elbow.x} y1={elbow.y} x2={wrist.x} y2={wrist.y} stroke="#66d9ff" strokeWidth="2" opacity={elbowConf} />
        {extendRatio > 0 ? (
          <line
            x1={wrist.x}
            y1={wrist.y}
            x2={extend.x}
            y2={extend.y}
            stroke="#ffd166"
            strokeWidth="2"
            strokeDasharray="5 3"
            opacity={elbowConf}
          />
        ) : null}
        <circle cx={elbow.x} cy={elbow.y} r="5" fill="#66d9ff" opacity={elbowConf} />
        <circle cx={wrist.x} cy={wrist.y} r="6" fill="#5dffa8" opacity={wristConf} />
        {extendRatio > 0 ? (
          <circle cx={extend.x} cy={extend.y} r="5" fill="#ffd166" opacity={wristConf * elbowConf} />
        ) : null}
        <text x="12" y="228" className="collision-diagram-legend">
          虚线框 = 软边界 (+{margin.toFixed(1)}px) · 绿点 = 腕 · 黄点 = 外推点 · 亮起 = 命中
        </text>
      </svg>
    </div>
  );
}

function CollisionGatingDiagram({ settings }) {
  const m = Math.round(num(settings, 'inference.collision.min_consecutive_frames', 3));
  const n = Math.max(m, Math.round(num(settings, 'inference.collision.window_frames', 6)));
  const cooldown = Math.round(num(settings, 'inference.collision.cooldown_frames', 6));

  const frames = Array.from({ length: n }, (_, i) => {
    const hit = i >= n - m;
    return { idx: i + 1, hit };
  });

  return (
    <div className="collision-diagram collision-diagram--gating">
      <div className="collision-diagram-head">
        <strong>告警门控示意图</strong>
        <span>最近 {n} 帧中至少 {m} 帧命中才告警；触发后冷却 {cooldown} 帧</span>
      </div>
      <div className="collision-gating-row">
        {frames.map((f) => (
          <div key={f.idx} className={`collision-gating-cell ${f.hit ? 'hit' : ''}`}>
            <span>{f.idx}</span>
          </div>
        ))}
      </div>
      <div className="collision-gating-note">
        {frames.filter((f) => f.hit).length >= m ? (
          <span className="ok">当前参数：窗口内 {frames.filter((f) => f.hit).length} 帧命中 → 可触发告警</span>
        ) : (
          <span>当前参数：窗口内 {frames.filter((f) => f.hit).length} 帧命中 → 未达 {m} 帧门槛</span>
        )}
      </div>
    </div>
  );
}

function CollisionTrackDiagram({ settings }) {
  const maxDist = num(settings, 'inference.collision.track_max_match_dist', 220);
  const staleSec = num(settings, 'inference.collision.track_stale_sec', 1.2);
  const r = Math.min(90, Math.max(24, maxDist * 0.32));

  return (
    <div className="collision-diagram collision-diagram--track">
      <div className="collision-diagram-head">
        <strong>人体跟踪示意图</strong>
        <span>anchor 在 {maxDist}px 内匹配同 track；{staleSec}s 无更新则 track 过期</span>
      </div>
      <svg viewBox="0 0 280 120" className="collision-diagram-svg" aria-label="人体跟踪示意图">
        <rect x="0" y="0" width="280" height="120" fill="#101820" rx="8" />
        <circle cx="92" cy="60" r={r} fill="rgba(93, 255, 168, 0.08)" stroke="rgba(93, 255, 168, 0.35)" strokeDasharray="4 3" />
        <circle cx="92" cy="60" r="8" fill="#5dffa8" />
        <text x="92" y="98" textAnchor="middle" className="collision-diagram-label">上一帧</text>
        <circle cx={92 + r * 0.55} cy="48" r="7" fill="#66d9ff" />
        <text x={92 + r * 0.55} y="98" textAnchor="middle" className="collision-diagram-label">本帧</text>
        <line x1="92" y1="60" x2={92 + r * 0.55} y2="48" stroke="#66d9ff" strokeWidth="1.5" />
      </svg>
    </div>
  );
}

function renderSliderField(field, settings, onChange) {
  const raw = settings[field.key];
  const value = raw === undefined || raw === null || raw === '' ? field.min : Number(raw);
  const step = field.step ?? 1;
  const display =
    step >= 1 ? Math.round(value) : Number(value.toFixed(step < 0.05 ? 2 : 1));

  return (
    <label key={field.key} className="collision-slider-field">
      <span className="settings-field-label">
        {field.label}
        <span className="collision-slider-value">{display}</span>
        {field.hint ? <FieldHint text={field.hint} /> : null}
      </span>
      <input
        type="range"
        min={field.min}
        max={field.max}
        step={step}
        value={value}
        onChange={(e) => onChange(field.key, Number(e.target.value))}
        className="collision-slider"
      />
      <span className="collision-slider-range">
        {field.min} – {field.max}
      </span>
    </label>
  );
}

export default function CollisionSettingsPanel() {
  const [settings, setSettings] = useState({});
  const [msg, setMsg] = useState('');
  const [loading, setLoading] = useState(true);

  const loadSettings = useCallback(async () => {
    setLoading(true);
    try {
      const data = await apiGet('/api/settings');
      if (data.items) setSettings(data.items);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadSettings();
  }, [loadSettings]);

  const collisionFields = useMemo(() => COLLISION_SETTINGS_FIELDS, []);
  const spatialFields = collisionFields.filter((f) => SPATIAL_KEYS.has(f.key));
  const gatingFields = collisionFields.filter((f) => GATING_KEYS.has(f.key));
  const trackFields = collisionFields.filter((f) => TRACK_KEYS.has(f.key));
  const boolField = collisionFields.find((f) => f.type === 'boolean');

  const patchSetting = (key, value) => {
    setSettings((s) => ({ ...s, [key]: value }));
  };

  const saveSettings = async (e) => {
    e.preventDefault();
    setMsg('');
    const payload = {};
    for (const f of collisionFields) {
      if (settings[f.key] !== undefined) payload[f.key] = settings[f.key];
    }
    try {
      const data = await apiPatch('/api/settings', payload);
      if (data.status !== 'success') {
        setMsg(formatUserError(data.error) || '保存失败');
        return;
      }
      if (data.items) setSettings(data.items);
      setMsg('已保存，Event Worker 将在数秒内自动加载');
    } catch (err) {
      setMsg(formatUserError(err.message) || '保存失败');
    }
  };

  if (loading) {
    return <div className="settings-panel collision-settings-panel">加载中…</div>;
  }

  return (
    <form className="settings-panel collision-settings-panel" onSubmit={saveSettings}>
      <p className="settings-panel-lead">
        调整碰撞检测阈值与告警门控。左侧<strong>示意图随滑块实时变化</strong>，便于理解命中范围；保存后 Event Worker 自动热加载。
      </p>

      <div className="collision-settings-layout">
        <div className="collision-settings-diagrams">
          <CollisionSpatialDiagram settings={settings} />
          <CollisionGatingDiagram settings={settings} />
          <CollisionTrackDiagram settings={settings} />
        </div>

        <div className="collision-settings-sliders">
          <h3 className="collision-slider-group-title">命中范围</h3>
          <div className="collision-slider-grid">
            {spatialFields.map((f) => renderSliderField(f, settings, patchSetting))}
          </div>

          <h3 className="collision-slider-group-title">告警门控</h3>
          <div className="collision-slider-grid">
            {gatingFields.map((f) => renderSliderField(f, settings, patchSetting))}
          </div>

          <h3 className="collision-slider-group-title">人体跟踪</h3>
          <div className="collision-slider-grid">
            {trackFields.map((f) => renderSliderField(f, settings, patchSetting))}
          </div>

          {boolField ? (
            <label className="collision-toggle-field">
              <span className="settings-field-label">
                {boolField.label}
                {boolField.hint ? <FieldHint text={boolField.hint} /> : null}
              </span>
              <span className="settings-toggle-field">
                <span className="settings-toggle">
                  <input
                    type="checkbox"
                    checked={Boolean(settings[boolField.key])}
                    onChange={(e) => patchSetting(boolField.key, e.target.checked)}
                  />
                  <span className="settings-toggle-track" aria-hidden="true" />
                </span>
              </span>
            </label>
          ) : null}
        </div>
      </div>

      <div className="settings-panel-footer">
        <button type="submit" className="settings-btn-primary">
          保存碰撞配置
        </button>
        {msg ? <p className={`settings-msg ${msg.includes('已') ? 'ok' : 'err'}`}>{msg}</p> : null}
      </div>
    </form>
  );
}
