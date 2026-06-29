import {
  DEFAULT_RTM_DET,
  RTMDET_OPTIONS,
  RTMPOSE_BACKEND_OPTIONS,
  YOLO_BACKEND_OPTIONS,
  backendShortLabel,
  detShortLabel,
  isRtmposeBackend,
  normalizeBackendId,
  normalizeDetId,
} from '../lib/cameraSettings';
import './InferenceModelFields.css';

/** 全局设置页：双下拉（RTMPose 时显示 det） */
export function InferenceModelGlobalFields({ backend, det, onBackendChange, onDetChange }) {
  const backendVal = normalizeBackendId(backend || 'rtmpose_t');
  const detVal = normalizeDetId(det);
  const showDet = isRtmposeBackend(backendVal);

  return (
    <div className="inference-model-fields">
      <label>
        <span className="settings-field-label">姿态模型</span>
        <select value={backendVal} onChange={(e) => onBackendChange(e.target.value)}>
          <optgroup label="RTMPose（top-down，需检测器）">
            {RTMPOSE_BACKEND_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.shortLabel || opt.label}
              </option>
            ))}
          </optgroup>
          <optgroup label="YOLO26-pose（bottom-up，端到端）">
            {YOLO_BACKEND_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.shortLabel || opt.label}
              </option>
            ))}
          </optgroup>
        </select>
      </label>
      {showDet ? (
        <label>
          <span className="settings-field-label">人体检测 (RTMDet)</span>
          <select value={detVal} onChange={(e) => onDetChange(e.target.value)}>
            {RTMDET_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      <p className="inference-model-hint">
        {showDet
          ? 'RTMPose 为 top-down 流程：先 RTMDet 检人，再 RTMPose 估姿态。修改后需重新启动智能检测。'
          : 'YOLO26-pose 为 bottom-up 端到端模型，无需单独选择检测器。修改后需重新启动智能检测。'}
      </p>
    </div>
  );
}

/** 摄像头抽屉：带「自定义」开关的双下拉 */
export function InferenceModelOverrideCard({
  settings,
  globalDefaults,
  effectiveSettings,
  onEnableCustom,
  onDisableCustom,
  onSetValue,
}) {
  const customized =
    Object.prototype.hasOwnProperty.call(settings, 'models.backend') ||
    Object.prototype.hasOwnProperty.call(settings, 'models.det');
  const globalBackend = normalizeBackendId(globalDefaults['models.backend'] || 'rtmpose_t');
  const globalDet = normalizeDetId(globalDefaults['models.det']);
  const currentBackend = normalizeBackendId(
    customized
      ? settings['models.backend'] ?? effectiveSettings['models.backend'] ?? globalBackend
      : globalBackend,
  );
  const currentDet = normalizeDetId(
    customized
      ? settings['models.det'] ?? effectiveSettings['models.det'] ?? globalDet
      : globalDet,
  );
  const showDet = isRtmposeBackend(currentBackend);

  const globalLabel = isRtmposeBackend(globalBackend)
    ? `${backendShortLabel(globalBackend)} + ${detShortLabel(globalDet)}`
    : backendShortLabel(globalBackend);

  return (
    <div
      className={`drawer-param-card inference-model-card${customized ? ' is-custom' : ''} drawer-param-card--wide`}
    >
      <div className="drawer-param-top">
        <span className="drawer-param-label">推理模型</span>
        <label className="drawer-param-custom">
          <input
            type="checkbox"
            checked={customized}
            onChange={(e) => (e.target.checked ? onEnableCustom() : onDisableCustom())}
          />
          <span className="drawer-param-custom-track" aria-hidden />
          <span className="drawer-param-custom-text">自定义</span>
        </label>
      </div>
      <div className="inference-model-fields inference-model-fields--drawer">
        <label className="inference-model-row">
          <span className="inference-model-row-label">姿态模型</span>
          <select
            className="drawer-param-input"
            disabled={!customized}
            value={currentBackend}
            onChange={(e) => onSetValue('models.backend', e.target.value)}
          >
            <optgroup label="RTMPose">
              {RTMPOSE_BACKEND_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.shortLabel || opt.label}
                </option>
              ))}
            </optgroup>
            <optgroup label="YOLO26-pose">
              {YOLO_BACKEND_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.shortLabel || opt.label}
                </option>
              ))}
            </optgroup>
          </select>
        </label>
        {showDet ? (
          <label className="inference-model-row">
            <span className="inference-model-row-label">人体检测 (RTMDet)</span>
            <select
              className="drawer-param-input"
              disabled={!customized}
              value={currentDet}
              onChange={(e) => onSetValue('models.det', e.target.value)}
            >
              {RTMDET_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.shortLabel || opt.label}
                </option>
              ))}
            </select>
          </label>
        ) : null}
      </div>
      <div className="drawer-param-foot">
        <span className="drawer-param-default">
          全局默认 <strong>{globalLabel}</strong>
        </span>
        <p className="drawer-param-hint">
          RTMPose 需 lite / lite-gpu-onnx 镜像；YOLO 需含 ultralytics 的 GPU 镜像。保存后请重新启动该路智能检测。
        </p>
      </div>
    </div>
  );
}
