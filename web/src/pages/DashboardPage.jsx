import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import CameraSetupDrawer from '../components/CameraSetupDrawer';
import InferenceToggle from '../components/InferenceToggle';
import { confirmDeleteCamera } from '../lib/confirmDelete';
import {
  apiDelete,
  apiGet,
  apiPost,
  apiPut,
  cameraPlaybackUrl,
  formatDuration,
  thumbnailUrl,
} from '../api/client';
import { captureThumbnailFromHls } from '../lib/captureFrameFromVideo';
import {
  STREAM_CONFIG_SAVED_HINT,
  formatInferenceMessage,
  formatUserError,
} from '../lib/userFacingText';
import './DashboardPage.css';

const POLL_MS = 30000;

const INFER_LABEL = {
  stopped: '检测未启动',
  running: '检测运行中',
  starting: '检测启动中',
  error: '检测异常',
  paused: '检测已暂停',
};

import {
  playbackUrlFieldFromCamera,
  streamUrlFromCamera,
  validateStreamUrl,
} from '../lib/cameraSource';

const emptyForm = () => ({
  path: '',
  name: '',
  source_type: 'external',
  stream_url: '',
  playback_url: '',
  enabled: true,
  settings: {},
});

export default function DashboardPage() {
  const navigate = useNavigate();
  const [cameras, setCameras] = useState([]);
  const [listLoading, setListLoading] = useState(true);
  const [probing, setProbing] = useState(false);
  const [msg, setMsg] = useState('');
  const [msgErr, setMsgErr] = useState(false);
  const [refreshingId, setRefreshingId] = useState(null);
  const [inferLoadingId, setInferLoadingId] = useState(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerMode, setDrawerMode] = useState('edit');
  const [setupCamera, setSetupCamera] = useState(null);
  /** 用户手动抓帧后的预览（data URL），不自动请求 /thumbnail */
  const [previewById, setPreviewById] = useState({});
  const [form, setForm] = useState(emptyForm());
  const [saving, setSaving] = useState(false);
  const [configHint, setConfigHint] = useState('');
  const [globalSettings, setGlobalSettings] = useState({});
  const [playbackDefault, setPlaybackDefault] = useState('');

  const applyConfigHint = (data) => {
    if (data?.reload_hint || data?.mediamtx?.reload_hint) {
      setConfigHint(STREAM_CONFIG_SAVED_HINT);
    }
  };

  const applyCameraItems = useCallback((items) => {
    if (!Array.isArray(items)) return false;
    const now = Date.now() / 1000;
    const mapped = items.map((item) => ({
      ...item,
      _syncedAt: now,
      _displayActivity: item.activity_seconds ?? 0,
    }));
    setCameras(mapped);
    setSetupCamera((prev) => {
      if (!prev) return prev;
      return mapped.find((c) => c.id === prev.id) || prev;
    });
    setMsg(`共 ${items.length} 路摄像头 · 上次更新 ${new Date().toLocaleTimeString()}`);
    setMsgErr(false);
    return true;
  }, []);

  const loadCameras = useCallback(async ({ probe = false } = {}) => {
    if (probe) setProbing(true);
    try {
      const qs = probe ? '' : '?probe=false';
      const data = await apiGet(`/api/cameras${qs}`);
      if (data.status !== 'success' || !Array.isArray(data.items)) {
        setMsg(formatUserError(data.error) || '加载失败');
        setMsgErr(true);
        return false;
      }
      applyCameraItems(data.items);
      return true;
    } catch (e) {
      setMsg(formatUserError(e.message) || '无法连接服务器');
      setMsgErr(true);
      return false;
    } finally {
      if (probe) {
        setProbing(false);
      } else {
        setListLoading(false);
      }
    }
  }, [applyCameraItems]);

  const refreshListFastThenProbe = useCallback(async () => {
    await loadCameras({ probe: false });
    void loadCameras({ probe: true });
  }, [loadCameras]);

  const refreshCamerasAfterMutation = useCallback(
    async (mutationData) => {
      if (applyCameraItems(mutationData?.items)) {
        setListLoading(false);
        void loadCameras({ probe: true });
        return;
      }
      await refreshListFastThenProbe();
    },
    [applyCameraItems, refreshListFastThenProbe],
  );

  useEffect(() => {
    let cancelled = false;
    (async () => {
      await loadCameras({ probe: false });
      if (!cancelled) void loadCameras({ probe: true });
    })();
    const poll = setInterval(() => loadCameras({ probe: false }), POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(poll);
    };
  }, [loadCameras]);

  useEffect(() => {
    const hasStarting = cameras.some((c) => c.inference?.status === 'starting');
    if (!hasStarting) return undefined;
    const fast = setInterval(() => loadCameras({ probe: false }), 5000);
    return () => clearInterval(fast);
  }, [cameras, loadCameras]);

  useEffect(() => {
    const tick = setInterval(() => {
      const now = Date.now() / 1000;
      setCameras((prev) =>
        prev.map((cam) => {
          if (!cam.online || !cam._syncedAt) {
            return { ...cam, _displayActivity: cam.activity_seconds };
          }
          const elapsed = Math.floor(now - cam._syncedAt);
          return { ...cam, _displayActivity: (cam.activity_seconds || 0) + elapsed };
        }),
      );
    }, 1000);
    return () => clearInterval(tick);
  }, []);

  const openCreate = () => {
    setDrawerMode('create');
    setSetupCamera(null);
    setForm(emptyForm());
    setDrawerOpen(true);
    loadGlobalSettings();
  };

  const loadGlobalSettings = useCallback(async () => {
    try {
      const data = await apiGet('/api/settings');
      if (data.items) setGlobalSettings(data.items);
    } catch {
      /* ignore */
    }
  }, []);

  const fetchPlaybackDefault = useCallback(async (path) => {
    const slug = String(path || '').trim();
    if (!slug) {
      setPlaybackDefault('');
      return '';
    }
    try {
      const data = await apiGet(
        `/api/cameras/playback-default?path=${encodeURIComponent(slug)}`,
      );
      const def = data?.playback_url || '';
      setPlaybackDefault(def);
      return def;
    } catch {
      setPlaybackDefault('');
      return '';
    }
  }, []);

  const openSetup = async (cam) => {
    setDrawerMode('edit');
    setSetupCamera(cam);
    setDrawerOpen(true);
    const path = cam.path || cam.id;
    const defEarly = await fetchPlaybackDefault(path);
    setForm({
      path,
      name: cam.name || '',
      source_type: cam.source_type || 'external',
      stream_url: streamUrlFromCamera(cam),
      playback_url: playbackUrlFieldFromCamera(cam, defEarly),
      enabled: cam.enabled !== false,
      settings: { ...(cam.settings || {}) },
    });
    let settings = { ...(cam.settings || {}) };
    let fullCam = cam;
    try {
      const detail = await apiGet(`/api/cameras/${encodeURIComponent(cam.id)}`);
      if (detail?.camera) {
        fullCam = detail.camera;
        setSetupCamera((prev) => ({ ...prev, ...fullCam }));
        settings = { ...(fullCam.settings || {}) };
        if (fullCam.global_defaults && typeof fullCam.global_defaults === 'object') {
          setGlobalSettings(fullCam.global_defaults);
        } else {
          await loadGlobalSettings();
        }
      } else {
        await loadGlobalSettings();
      }
    } catch {
      await loadGlobalSettings();
    }
    const def =
      fullCam.default_playback_url ||
      (await fetchPlaybackDefault(fullCam.path || fullCam.id));
    setForm({
      path: fullCam.path || fullCam.id,
      name: fullCam.name || '',
      source_type: fullCam.source_type || 'external',
      stream_url: streamUrlFromCamera(fullCam),
      playback_url: playbackUrlFieldFromCamera(fullCam, def),
      enabled: fullCam.enabled !== false,
      settings,
    });
  };

  const closeDrawer = () => {
    setDrawerOpen(false);
    setSetupCamera(null);
  };

  useEffect(() => {
    if (!drawerOpen) return undefined;
    const onKey = (e) => {
      if (e.key === 'Escape') closeDrawer();
    };
    document.addEventListener('keydown', onKey);
    document.body.style.overflow = 'hidden';
    if (form.path) {
      void fetchPlaybackDefault(form.path);
    }
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = '';
    };
  }, [drawerOpen, fetchPlaybackDefault, form.path]);

  useEffect(() => {
    if (!drawerOpen || drawerMode !== 'create') return;
    void fetchPlaybackDefault(form.path);
  }, [drawerOpen, drawerMode, form.path, fetchPlaybackDefault]);

  const onFormChange = (field, value) => {
    setForm((prev) => {
      const next = { ...prev, [field]: value };
      if (field === 'path') {
        void fetchPlaybackDefault(value);
      }
      return next;
    });
  };

  const saveFromDrawer = async () => {
    const sourceType = form.source_type || 'external';
    const stream = form.stream_url.trim();
    const customPlayback = form.playback_url.trim();

    if (sourceType === 'rtsp_pull') {
      if (!stream) {
        alert('请填写上游 RTSP 地址');
        return;
      }
      const pullErr = validateStreamUrl(stream);
      if (pullErr) {
        alert(pullErr);
        return;
      }
    } else {
      const mainErr = validateStreamUrl(stream);
      if (stream && mainErr) {
        alert(mainErr);
        return;
      }
    }
    if (customPlayback) {
      const pbErr = validateStreamUrl(customPlayback);
      if (pbErr) {
        alert(pbErr);
        return;
      }
    }

    const payload = {
      path: form.path,
      name: form.name,
      source_type: sourceType,
      enabled: form.enabled,
      settings: form.settings || {},
      playback_url: customPlayback,
    };
    if (sourceType === 'rtsp_pull') {
      payload.pull_url = stream;
      payload.url = '';
    } else {
      payload.url = stream;
      payload.pull_url = '';
    }
    setSaving(true);
    try {
      const data =
        drawerMode === 'create'
          ? await apiPost('/api/cameras', payload)
          : await apiPut(`/api/cameras/${encodeURIComponent(setupCamera.id)}`, payload);
      if (data.error) {
        alert(formatUserError(data.error));
        return;
      }
      applyConfigHint(data);
      closeDrawer();
      await refreshCamerasAfterMutation(data);
    } catch (err) {
      alert(formatUserError(err.message) || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const deleteFromDrawer = async () => {
    if (!setupCamera) return;
    if (!confirmDeleteCamera(setupCamera.name)) return;
    setSaving(true);
    try {
      const data = await apiDelete(`/api/cameras/${encodeURIComponent(setupCamera.id)}`);
      if (data.error) {
        alert(formatUserError(data.error));
        return;
      }
      applyConfigHint(data);
      closeDrawer();
      await refreshCamerasAfterMutation(data);
    } catch (err) {
      alert(formatUserError(err.message) || '删除失败');
    } finally {
      setSaving(false);
    }
  };

  const startInference = async (cam) => {
    setInferLoadingId(cam.id);
    try {
      const data = await apiPost(`/api/cameras/${encodeURIComponent(cam.id)}/inference/start`, {});
      if (data.error) {
        alert(formatUserError(data.error));
        return;
      }
      await loadCameras();
    } catch (e) {
      alert(formatUserError(e.message) || '启动检测失败');
    } finally {
      setInferLoadingId(null);
    }
  };

  const toggleInference = async (cam, turnOn) => {
    if (turnOn) await startInference(cam);
    else await stopInference(cam);
  };

  const stopInference = async (cam) => {
    setInferLoadingId(cam.id);
    try {
      const data = await apiPost(`/api/cameras/${encodeURIComponent(cam.id)}/inference/stop`, {});
      if (data.error) {
        alert(formatUserError(data.error));
        return;
      }
      await loadCameras();
    } catch (e) {
      alert(formatUserError(e.message) || '停止检测失败');
    } finally {
      setInferLoadingId(null);
    }
  };

  const openMonitor = (cam) => {
    navigate(`/monitor?camera=${encodeURIComponent(cam.id)}`);
  };

  const captureFrame = async (cam) => {
    setRefreshingId(cam.id);
    try {
      const pb = await apiGet(cameraPlaybackUrl(cam.id));
      const hlsUrl = pb?.formats?.hls?.url;
      if (!pb || pb.status !== 'success' || !hlsUrl || !pb.formats?.hls?.available) {
        alert(formatUserError(pb?.error) || '无法抓帧：HLS 预览不可用');
        return;
      }
      const image = await captureThumbnailFromHls(hlsUrl);
      const data = await apiPost(`/api/cameras/${encodeURIComponent(cam.id)}/capture`, { image });
      if (data.status !== 'success') {
        alert(formatUserError(data.error) || '抓帧失败');
        return;
      }
      if (data.image) {
        setPreviewById((prev) => ({
          ...prev,
          [cam.id]: `data:image/jpeg;base64,${data.image}`,
        }));
      }
      const patch = {
        has_thumbnail: true,
        last_frame_at: data.last_frame_at ?? Date.now() / 1000,
        online: data.online ?? cam.online,
        activity_seconds: data.activity_seconds ?? cam.activity_seconds,
      };
      setCameras((prev) => prev.map((c) => (c.id === cam.id ? { ...c, ...patch } : c)));
      setSetupCamera((prev) => (prev?.id === cam.id ? { ...prev, ...patch } : prev));
    } catch (e) {
      alert(formatUserError(e.message) || '抓帧失败');
    } finally {
      setRefreshingId(null);
    }
  };

  const drawerActionLoading = inferLoadingId === setupCamera?.id || refreshingId === setupCamera?.id;
  const drawerCamera = setupCamera
    ? cameras.find((c) => c.id === setupCamera.id) || setupCamera
    : null;

  return (
    <div className="page dashboard-page">
        <h1 className="page-title">摄像头总览</h1>

        <div className="toolbar">
          <span className={`msg ${msgErr ? 'err' : ''}`}>
            {listLoading ? '加载列表…' : msg}
            {probing && !listLoading ? ' · 正在探测在线状态' : ''}
          </span>
          <div className="toolbar-actions">
            <button
              type="button"
              className="btn-icon btn-icon-primary"
              title="添加摄像头"
              aria-label="添加摄像头"
              onClick={openCreate}
            >
              +
            </button>
            <button
              type="button"
              className="btn-icon"
              title="刷新列表"
              aria-label="刷新列表"
              disabled={listLoading || probing}
              onClick={() => refreshListFastThenProbe()}
            >
              ↻
            </button>
          </div>
        </div>

        {configHint && <div className="config-hint">{configHint}</div>}

        <div className="grid">
          {listLoading ? (
            <div className="empty grid-status">加载中…</div>
          ) : !cameras.length ? (
            <div className="empty">暂无摄像头，点击「添加摄像头」开始配置。</div>
          ) : (
            cameras.map((cam) => (
              <article className="card" key={cam.id}>
                <div
                  className="card-preview card-preview-link"
                  role="button"
                  tabIndex={0}
                  title="进入检测监控"
                  onClick={() => openMonitor(cam)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      openMonitor(cam);
                    }
                  }}
                >
                  {previewById[cam.id] ? (
                    <img src={previewById[cam.id]} alt={cam.name} />
                  ) : cam.has_thumbnail ? (
                    <img src={thumbnailUrl(cam.id, cam.last_frame_at)} alt={cam.name} />
                  ) : (
                    <div className="card-preview-empty">点击 ↻ 抓帧预览</div>
                  )}
                  <div className="card-actions" onClick={(e) => e.stopPropagation()}>
                    <InferenceToggle
                      on={
                        cam.inference?.status === 'running' ||
                        cam.inference?.status === 'starting'
                      }
                      loading={inferLoadingId === cam.id}
                      disabled={inferLoadingId === cam.id}
                      title={
                        cam.inference?.status === 'running' || cam.inference?.status === 'starting'
                          ? '关闭智能检测'
                          : '开启智能检测'
                      }
                      onToggle={(turnOn) => toggleInference(cam, turnOn)}
                    />
                    <button
                      type="button"
                      className="btn-icon"
                      title="抓帧"
                      disabled={refreshingId === cam.id}
                      onClick={(e) => {
                        e.stopPropagation();
                        captureFrame(cam);
                      }}
                    >
                      ↻
                    </button>
                    <button
                      type="button"
                      className="btn-icon"
                      title="设置"
                      onClick={() => openSetup(cam)}
                    >
                      ⚙
                    </button>
                  </div>
                  <div className="card-body">
                    <h2 className="card-title">{cam.name}</h2>
                    <div className="card-status">
                      <span className={cam.online ? 'st-online' : 'st-offline'}>
                        {cam.online ? '在线' : '离线'}
                      </span>
                      <span className="card-status-sep">·</span>
                      <span className="card-activity">{formatDuration(cam._displayActivity)}</span>
                      <span className="card-status-sep">·</span>
                      <span
                        className={`card-infer ${cam.inference?.status || 'stopped'}`}
                        title={formatInferenceMessage(cam.inference?.message) || ''}
                      >
                        {INFER_LABEL[cam.inference?.status] || INFER_LABEL.stopped}
                      </span>
                    </div>
                    <div className="card-url" title={cam.url}>
                      {cam.url}
                    </div>
                  </div>
                </div>
              </article>
            ))
          )}
        </div>

      <CameraSetupDrawer
        open={drawerOpen}
        mode={drawerMode}
        camera={drawerCamera}
        previewSrc={drawerCamera ? previewById[drawerCamera.id] : null}
        form={form}
        playbackDefault={playbackDefault}
        onChange={onFormChange}
        globalDefaults={globalSettings}
        effectiveSettings={drawerCamera?.effective_settings || {}}
        onClose={closeDrawer}
        onSave={saveFromDrawer}
        onDelete={deleteFromDrawer}
        onCapture={() => drawerCamera && captureFrame(drawerCamera)}
        onToggleInference={(turnOn) => drawerCamera && toggleInference(drawerCamera, turnOn)}
        saving={saving}
        actionLoading={drawerActionLoading}
      />
    </div>
  );
}
