import { useCallback, useEffect, useState } from 'react';
import { apiGet } from '../api/client';
import { backendLabel } from '../lib/cameraSettings';
import './ServiceOverviewPage.css';

const STATUS_LABEL = {
  running: '运行中',
  stopped: '已停止',
  starting: '启动中',
  error: '异常',
  paused: '已暂停',
};

function StatusBadge({ status }) {
  const s = status || 'unknown';
  return <span className={`svc-badge svc-badge--${s}`}>{STATUS_LABEL[s] || s}</span>;
}

function metricCell(value, suffix = '') {
  if (value == null || value === '') return '—';
  return `${value}${suffix}`;
}

export default function ServiceOverviewPage() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(async ({ silent = false } = {}) => {
    if (!silent) setLoading(true);
    setError('');
    try {
      const res = await apiGet('/api/services/overview');
      if (res?.status !== 'success') {
        setError(res?.message || '加载失败');
        setData(null);
        return;
      }
      setData(res);
    } catch (e) {
      setError(e?.message || '加载失败');
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(() => load({ silent: true }), 15000);
    return () => clearInterval(t);
  }, [load]);

  const host = data?.host || {};
  const stack = data?.stack || [];
  const redis = data?.redis || {};
  const mtx = data?.mediamtx || {};
  const cameras = data?.cameras || [];

  return (
    <div className="svc-page">
      <header className="svc-header">
        <h1>服务总览</h1>
        <button type="button" className="svc-refresh" onClick={load} disabled={loading}>
          {loading ? '刷新中…' : '刷新'}
        </button>
      </header>

      {error && <p className="svc-error">{error}</p>}

      {(host.cpu_percent != null || host.memory_percent != null) && (
        <p className="svc-host">
          宿主机（UI 进程视角）：
          {host.cpu_percent != null && ` CPU ${host.cpu_percent}%`}
          {host.memory_percent != null &&
            ` · 内存 ${host.memory_used_mb ?? '—'} / ${host.memory_total_mb ?? '—'} MB (${host.memory_percent}%)`}
        </p>
      )}

      <section className="svc-section">
        <h2>基础设施</h2>
        <table className="svc-table">
          <thead>
            <tr>
              <th>服务</th>
              <th>状态</th>
              <th>镜像 / 说明</th>
              <th>CPU</th>
              <th>内存</th>
            </tr>
          </thead>
          <tbody>
            {stack.map((row) => (
              <tr key={row.name}>
                <td>{row.name}</td>
                <td>
                  <StatusBadge status={row.status} />
                </td>
                <td>{row.image || row.message || '—'}</td>
                <td>{metricCell(row.cpu_percent, '%')}</td>
                <td>
                  {row.memory_mb != null
                    ? `${row.memory_mb}${row.memory_limit_mb != null ? ` / ${row.memory_limit_mb}` : ''} MB`
                    : '—'}
                </td>
              </tr>
            ))}
            <tr>
              <td>Redis</td>
              <td>
                <span className={redis.ok ? 'svc-ok' : 'svc-fail'}>
                  {redis.ok ? '可达' : '不可用'}
                </span>
              </td>
              <td colSpan={3}>{redis.message || (redis.ok ? 'PING OK' : '')}</td>
            </tr>
            <tr>
              <td>MediaMTX</td>
              <td>
                <span className={mtx.ok ? 'svc-ok' : 'svc-fail'}>
                  {mtx.ok ? 'API 正常' : 'API 异常'}
                </span>
              </td>
              <td colSpan={3}>
                {mtx.message || ''}
                {mtx.paths?.length
                  ? ` · ${mtx.paths.filter((p) => p.ready).length}/${mtx.paths.length} path 就绪`
                  : ''}
              </td>
            </tr>
          </tbody>
        </table>
      </section>

      <section className="svc-section">
        <h2>摄像头与推理</h2>
        <table className="svc-table svc-table--infer">
          <thead>
            <tr>
              <th>摄像头</th>
              <th>在线</th>
              <th>流就绪</th>
              <th>推理</th>
              <th>后端</th>
              <th>FPS</th>
              <th>CPU</th>
              <th>内存</th>
              <th>GPU</th>
              <th>说明</th>
            </tr>
          </thead>
          <tbody>
            {cameras.length === 0 && !loading && (
              <tr>
                <td colSpan={10}>暂无摄像头</td>
              </tr>
            )}
            {cameras.map((cam) => {
              const inf = cam.inference || {};
              const m = inf.metrics || {};
              const cpu = m.container_cpu_percent ?? m.cpu_percent;
              const mem = m.container_memory_mb ?? m.memory_mb;
              const gpu =
                m.gpu_util_percent != null
                  ? `${m.gpu_util_percent}%`
                  : m.gpu_memory_mb != null
                    ? `${m.gpu_memory_mb} MB`
                    : m.gpu_memory_used_mb != null
                      ? `${m.gpu_memory_used_mb} MB`
                      : null;
              return (
                <tr key={cam.id}>
                  <td>{cam.name || cam.id}</td>
                  <td>{cam.online == null ? '—' : cam.online ? '是' : '否'}</td>
                  <td>{cam.stream_ready == null ? '—' : cam.stream_ready ? '是' : '否'}</td>
                  <td>
                    <StatusBadge status={inf.status} />
                  </td>
                  <td>{backendLabel(inf.backend) || '—'}</td>
                  <td>{metricCell(m.fps)}</td>
                  <td>{metricCell(cpu ?? m.cpu_percent, '%')}</td>
                  <td>{mem != null ? `${mem} MB` : m.memory_mb != null ? `${m.memory_mb} MB` : '—'}</td>
                  <td>{gpu ?? '—'}</td>
                  <td className="svc-msg">{inf.message || inf.container || '—'}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <p className="svc-hint">
          推理 FPS/进程指标来自 worker 状态文件；CPU/内存优先显示 Docker 容器 cgroup。未启动推理时显示为 —。
        </p>
      </section>

      {mtx.paths?.length > 0 && (
        <section className="svc-section">
          <h2>MediaMTX 路径</h2>
          <table className="svc-table">
            <thead>
              <tr>
                <th>Path</th>
                <th>就绪</th>
                <th>读者数</th>
              </tr>
            </thead>
            <tbody>
              {mtx.paths.map((p) => (
                <tr key={p.name}>
                  <td>{p.name}</td>
                  <td>{p.ready ? '是' : '否'}</td>
                  <td>{p.readers ?? 0}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}
