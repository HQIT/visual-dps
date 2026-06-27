import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import MonitorPreviewStage from '../components/MonitorPreviewStage';
import { apiDelete, apiGet, apiPost } from '../api/client';
import { parseAnnotationPayload } from '../lib/annotation';
import { formatUserError } from '../lib/userFacingText';
import './BenchPage.css';

const BACKENDS = [
  { id: 'rtmpose_t', label: 'RTMPose-T' },
  { id: 'rtmpose_s', label: 'RTMPose-S' },
  { id: 'rtmpose_m', label: 'RTMPose-M' },
];

function statusLabel(status) {
  const map = {
    pending: '等待中',
    running: '运行中',
    finished: '已完成',
    error: '失败',
    cancelled: '已取消',
  };
  return map[status] || status || '—';
}

export default function BenchPage() {
  const [cameras, setCameras] = useState([]);
  const [runs, setRuns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [cameraId, setCameraId] = useState('cam1');
  const [backend, setBackend] = useState('rtmpose_t');
  const [poseFrameInterval, setPoseFrameInterval] = useState(3);
  const [title, setTitle] = useState('');
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState(null);
  const [previewMediaUrl, setPreviewMediaUrl] = useState('');
  const [previewLoading, setPreviewLoading] = useState(false);
  const [submitLoading, setSubmitLoading] = useState(false);
  const [rerunLoadingId, setRerunLoadingId] = useState('');

  const annotation = useMemo(() => {
    if (!preview?.annotation) {
      return { boxes: [], shelves: [], shelfCorners: [], annotationSize: null, gridShape: [] };
    }
    return parseAnnotationPayload(preview.annotation);
  }, [preview]);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [camRes, runRes, settingsRes] = await Promise.all([
        apiGet('/api/cameras'),
        apiGet('/api/benchmark/runs?page_size=50'),
        apiGet('/api/settings'),
      ]);
      setCameras(Array.isArray(camRes.items) ? camRes.items : camRes.cameras || []);
      setRuns(runRes.items || []);
      const defaultInterval = Number(settingsRes?.items?.['inference.pose_frame_interval']);
      if (Number.isFinite(defaultInterval) && defaultInterval >= 1) {
        setPoseFrameInterval(Math.round(defaultInterval));
      }
    } catch (err) {
      setError(formatUserError(err.message) || '加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
    const timer = setInterval(loadData, 5000);
    return () => clearInterval(timer);
  }, [loadData]);

  useEffect(() => () => {
    if (previewMediaUrl) URL.revokeObjectURL(previewMediaUrl);
  }, [previewMediaUrl]);

  const handlePreview = async () => {
    if (!file || !cameraId) return alert('请选择摄像头并上传视频');
    setPreviewLoading(true);
    setError('');
    try {
      if (previewMediaUrl) URL.revokeObjectURL(previewMediaUrl);
      const objectUrl = URL.createObjectURL(file);
      setPreviewMediaUrl(objectUrl);

      const fd = new FormData();
      fd.append('camera_id', cameraId);
      fd.append('file', file);
      const resp = await fetch('/api/benchmark/preview', { method: 'POST', credentials: 'include', body: fd });
      const data = await resp.json();
      if (data.status !== 'success') {
        setError(formatUserError(data.error) || '预览失败');
        setPreview(null);
        return;
      }
      setPreview(data);
    } catch (err) {
      setError(formatUserError(err.message) || '预览失败');
    } finally {
      setPreviewLoading(false);
    }
  };

  const handleCreate = async () => {
    if (!file || !cameraId) return alert('请选择摄像头并上传视频');
    setSubmitLoading(true);
    setError('');
    try {
      const fd = new FormData();
      fd.append('camera_id', cameraId);
      fd.append('backend', backend);
      fd.append('pose_frame_interval', String(Math.max(1, Math.min(120, Number(poseFrameInterval) || 3))));
      fd.append('title', title || file.name || '');
      fd.append('start', 'true');
      fd.append('file', file);
      const resp = await fetch('/api/benchmark/runs', { method: 'POST', credentials: 'include', body: fd });
      const data = await resp.json();
      if (data.status !== 'success') {
        setError(formatUserError(data.error) || '创建失败');
        return;
      }
      setFile(null);
      setPreview(null);
      if (previewMediaUrl) {
        URL.revokeObjectURL(previewMediaUrl);
        setPreviewMediaUrl('');
      }
      setTitle('');
      await loadData();
    } catch (err) {
      setError(formatUserError(err.message) || '创建失败');
    } finally {
      setSubmitLoading(false);
    }
  };

  const handleRerun = async (run) => {
    let effectiveInterval = run.config?.['inference.pose_frame_interval'];
    let effectiveBackend = run.backend;
    try {
      const settingsRes = await apiGet('/api/settings');
      const items = settingsRes?.items || {};
      if (items['inference.pose_frame_interval'] != null) {
        effectiveInterval = items['inference.pose_frame_interval'];
      }
      if (items['models.backend']) {
        effectiveBackend = items['models.backend'];
      }
    } catch {
      /* 回退 run 快照 */
    }
    const detail = [
      `模型：${effectiveBackend || '—'}`,
      effectiveInterval != null ? `姿态间隔：${effectiveInterval}（系统当前）` : null,
      `摄像头：${run.camera_id || '—'}`,
    ].filter(Boolean).join('\n');
    if (!window.confirm(`按系统当前参数重跑该评测？\n\n${detail}\n\n将清空原有 pose/告警结果。`)) return;
    setRerunLoadingId(run.id);
    setError('');
    try {
      const res = await apiPost(`/api/benchmark/runs/${encodeURIComponent(run.id)}/rerun`, {});
      if (res.status !== 'success') {
        setError(formatUserError(res.error) || res.message || '重跑失败');
        return;
      }
      await loadData();
    } catch (err) {
      setError(formatUserError(err.message) || '重跑失败');
    } finally {
      setRerunLoadingId('');
    }
  };

  const handleDelete = async (runId) => {
    if (!window.confirm('确定删除该评测记录？')) return;
    const res = await apiDelete(`/api/benchmark/runs/${encodeURIComponent(runId)}`);
    if (res.status !== 'success') {
      alert(formatUserError(res.error) || '删除失败');
      return;
    }
    loadData();
  };

  const previewReady = preview && previewMediaUrl;

  return (
    <div className="bench-page">
      <header className="bench-header">
        <div>
          <h1>离线评测</h1>
          <p>上传视频、对齐标注后进行骨骼推理与碰撞检测，结果可回放。</p>
        </div>
      </header>

      {error ? <div className="bench-error">{error}</div> : null}

      <section className="bench-card">
        <h2>新建评测</h2>
        <div className="bench-form-grid">
          <label>
            绑定标注（摄像头）
            <select value={cameraId} onChange={(e) => { setCameraId(e.target.value); setPreview(null); }}>
              {cameras.map((c) => (
                <option key={c.id} value={c.id}>{c.name || c.id}</option>
              ))}
            </select>
          </label>
          <label>
            推理模型
            <select value={backend} onChange={(e) => setBackend(e.target.value)}>
              {BACKENDS.map((b) => (
                <option key={b.id} value={b.id}>{b.label}</option>
              ))}
            </select>
          </label>
          <label>
            姿态间隔（帧）
            <input
              type="number"
              min={1}
              max={120}
              step={1}
              value={poseFrameInterval}
              onChange={(e) => setPoseFrameInterval(Number(e.target.value) || 1)}
            />
            <span className="bench-field-hint">每 N 帧做一次姿态推理；1=每帧，3=默认跳帧</span>
          </label>
          <label>
            标题（可选）
            <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="例如 clip3-part1" />
          </label>
          <label>
            测试视频
            <input
              type="file"
              accept="video/*"
              onChange={(e) => {
                const next = e.target.files?.[0] || null;
                if (previewMediaUrl) URL.revokeObjectURL(previewMediaUrl);
                setPreviewMediaUrl('');
                setFile(next);
                setPreview(null);
              }}
            />
          </label>
        </div>
        <div className="bench-actions">
          <button type="button" disabled={!file || previewLoading} onClick={handlePreview}>
            {previewLoading ? '预览中…' : '对齐预览'}
          </button>
          <button type="button" className="primary" disabled={!file || submitLoading} onClick={handleCreate}>
            {submitLoading ? '提交中…' : '开始评测'}
          </button>
        </div>

        {previewReady ? (
          <div className="bench-preview-wrap">
            <div className={`bench-alignment ${preview.size_match ? 'ok' : 'warn'}`}>
              视频 {preview.video?.width}×{preview.video?.height} ·
              标注 {preview.annotation_size?.width}×{preview.annotation_size?.height} ·
              {preview.alignment_hint}
            </div>
            <div className="bench-legend">
              <span className="bench-legend-item"><i className="bench-legend-dot is-configured" />已配置</span>
              <span className="bench-legend-item"><i className="bench-legend-dot is-monitoring" />监测中</span>
              <span className="bench-legend-item"><i className="bench-legend-dot is-hit" />碰撞</span>
              <span className="bench-legend-item"><i className="bench-legend-dot is-alarm" />告警</span>
            </div>
            <MonitorPreviewStage
              compactPreview
              filePreviewUrl={previewMediaUrl}
              boxes={annotation.boxes}
              shelves={annotation.shelves}
              gridShape={annotation.gridShape}
              shelfCorners={annotation.shelfCorners}
              annotationSize={annotation.annotationSize}
              showRoiLayer
              showSkeletonLayer={false}
              inferRunning={false}
              emptyText="正在加载预览帧…"
            />
          </div>
        ) : null}
      </section>

      <section className="bench-card">
        <h2>历史记录</h2>
        {loading && !runs.length ? <p>加载中…</p> : null}
        <table className="bench-table">
          <thead>
            <tr>
              <th>标题</th>
              <th>摄像头</th>
              <th>模型</th>
              <th>间隔</th>
              <th>状态</th>
              <th>Pose帧</th>
              <th>告警</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr key={run.id}>
                <td>{run.title || run.id}</td>
                <td>{run.camera_id}</td>
                <td>{run.backend}</td>
                <td>{run.config?.['inference.pose_frame_interval'] ?? '—'}</td>
                <td><span className={`bench-status is-${run.status}`}>{statusLabel(run.status)}</span></td>
                <td>{run.pose_frame_count ?? 0}</td>
                <td>{run.alarm_count ?? 0}</td>
                <td className="bench-row-actions">
                  <Link to={`/bench/${encodeURIComponent(run.id)}`}>回放</Link>
                  {run.status !== 'running' ? (
                    <button
                      type="button"
                      className="linkish"
                      disabled={rerunLoadingId === run.id}
                      onClick={() => handleRerun(run)}
                    >
                      {rerunLoadingId === run.id ? '重跑中…' : '重跑'}
                    </button>
                  ) : null}
                  <button type="button" className="linkish" onClick={() => handleDelete(run.id)}>删除</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!loading && !runs.length ? <p className="bench-empty">暂无评测记录</p> : null}
      </section>
    </div>
  );
}
