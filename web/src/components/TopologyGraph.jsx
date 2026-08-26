import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { edgeBelongsToCamera } from '../lib/topologyEdges';

const LAYER_ORDER = [
  { key: 'source', title: '流媒体源' },
  { key: 'mediamtx', title: 'MediaMTX' },
  { key: 'inference', title: '推理' },
  { key: 'redis', title: 'Redis' },
  { key: 'event_worker', title: '事件' },
];

const KIND_TO_LAYER = {
  source: 'source',
  mediamtx: 'mediamtx',
  mediamtx_path: 'mediamtx',
  inference: 'inference',
  redis: 'redis',
  event_worker: 'event_worker',
  ui: 'mediamtx',
};

function nodeSubtitle(node) {
  if (node.kind === 'inference') {
    const st = String(node.meta?.docker_status || '').toLowerCase();
    if (st === 'stopped' || !st) return '检测未启动';
    if (st === 'starting') return '启动中';
  }
  if (node.kind === 'event_worker' && node.meta?.shard_label) {
    return `shard ${node.meta.shard_label}`;
  }
  const parts = [];
  if (node.hostname) parts.push(node.hostname);
  if (node.ip) parts.push(node.ip);
  const port = (node.ports || []).find((p) => p.host_port || p.container_port);
  if (port) {
    const p = port.host_port || port.container_port;
    parts.push(`:${p}`);
  }
  return parts.join(' · ') || node.kind;
}

export default function TopologyGraph({
  nodes,
  edges,
  focusCameraId = '',
  selectedNodeId,
  onSelectNode,
}) {
  const wrapRef = useRef(null);
  const nodeRefs = useRef({});
  const [lines, setLines] = useState([]);

  const setNodeRef = useCallback((id) => {
    return (el) => {
      if (el) nodeRefs.current[id] = el;
      else delete nodeRefs.current[id];
    };
  }, []);

  const recomputeLines = useCallback(() => {
    const wrap = wrapRef.current;
    if (!wrap) return;
    const rect = wrap.getBoundingClientRect();
    const next = [];
    for (const edge of edges || []) {
      const fromEl = nodeRefs.current[edge.from];
      const toEl = nodeRefs.current[edge.to];
      if (!fromEl || !toEl) continue;
      const fr = fromEl.getBoundingClientRect();
      const tr = toEl.getBoundingClientRect();
      const x1 = fr.right - rect.left;
      const y1 = fr.top + fr.height / 2 - rect.top;
      const x2 = tr.left - rect.left;
      const y2 = tr.top + tr.height / 2 - rect.top;
      const mid = (x1 + x2) / 2;
      const focused = !focusCameraId || edgeBelongsToCamera(edge, focusCameraId);
      next.push({
        id: edge.id,
        d: `M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`,
        health: edge.health,
        focused,
      });
    }
    setLines(next);
  }, [edges, focusCameraId]);

  useLayoutEffect(() => {
    recomputeLines();
    const ro = new ResizeObserver(() => recomputeLines());
    if (wrapRef.current) ro.observe(wrapRef.current);
    window.addEventListener('resize', recomputeLines);
    return () => {
      ro.disconnect();
      window.removeEventListener('resize', recomputeLines);
    };
  }, [recomputeLines, nodes, edges, focusCameraId]);

  useEffect(() => {
    const t = setTimeout(recomputeLines, 80);
    return () => clearTimeout(t);
  }, [recomputeLines, focusCameraId]);

  const byLayer = {};
  for (const layer of LAYER_ORDER) {
    byLayer[layer.key] = [];
  }
  for (const node of nodes || []) {
    const layer = KIND_TO_LAYER[node.kind];
    if (layer && byLayer[layer]) {
      byLayer[layer].push(node);
    }
  }

  return (
    <div className="topology-graph-wrap" ref={wrapRef}>
      <svg className="topology-svg" aria-hidden>
        {lines.map((ln) => (
          <path
            key={ln.id}
            d={ln.d}
            fill="none"
            stroke={
              ln.health === 'ok'
                ? '#2d6a4f'
                : ln.health === 'warn'
                  ? '#e9c46a'
                  : ln.health === 'error'
                    ? '#9d0208'
                    : '#4a5f73'
            }
            strokeWidth={ln.focused && focusCameraId ? 2.5 : 1.5}
            opacity={ln.focused ? 0.9 : 0.18}
          />
        ))}
      </svg>
      <div className="topology-layers">
        {LAYER_ORDER.map((layer) => (
          <div key={layer.key} className="topology-layer-col">
            <div className="topology-layer-title">{layer.title}</div>
            <div className="topology-layer">
              {(byLayer[layer.key] || []).map((node) => (
                <button
                  key={node.id}
                  type="button"
                  ref={setNodeRef(node.id)}
                  className={`topology-node ${node.health || 'unknown'}${selectedNodeId === node.id ? ' selected' : ''}`}
                  onClick={() => onSelectNode?.(node)}
                >
                  <div className="topology-node-label">{node.label}</div>
                  <div className="topology-node-meta">{nodeSubtitle(node)}</div>
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
