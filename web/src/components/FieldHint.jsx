import './FieldHint.css';

/** 字段说明：悬停/聚焦 info 图标显示，不占表单项下方空间 */
export default function FieldHint({ text, className = '' }) {
  if (!text) return null;
  return (
    <span
      className={`field-hint-info ${className}`.trim()}
      tabIndex={0}
      role="button"
      aria-label={text}
    >
      <span className="field-hint-info-icon" aria-hidden="true">
        ?
      </span>
      <span className="field-hint-info-tooltip" role="tooltip">
        {text}
      </span>
    </span>
  );
}
