import { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../context/AuthContext';
import { apiDelete, apiGet, apiPatch, apiPost } from '../api/client';
import LogsPanel from '../components/LogsPanel';
import ConfirmDialog from '../components/ConfirmDialog';
import UserDrawer from '../components/UserDrawer';
import FieldHint from '../components/FieldHint';
import { CAMERA_OVERRIDE_FIELDS, GLOBAL_COLLISION_FIELDS, GLOBAL_PIPELINE_LOG_FIELDS, GLOBAL_PREFILTER_FIELDS, formatFieldDefaultValue, resolveSettingValue, settingsFieldTooltip } from '../lib/cameraSettings';
import { InferenceModelGlobalFields } from '../components/InferenceModelFields';
import { formatUserError } from '../lib/userFacingText';
import './SettingsPage.css';

export default function SettingsPage() {
  const { user } = useAuth();
  const isAdmin = Boolean(user?.is_admin);
  const [tab, setTab] = useState(isAdmin ? 'system' : 'password');

  const [pwd, setPwd] = useState({ old: '', next: '', confirm: '' });
  const [pwdMsg, setPwdMsg] = useState('');

  const [settings, setSettings] = useState({});
  const [settingsMsg, setSettingsMsg] = useState('');

  const [users, setUsers] = useState([]);
  const [usersLoading, setUsersLoading] = useState(false);
  const [usersMsg, setUsersMsg] = useState('');
  const [usersMsgErr, setUsersMsgErr] = useState(false);
  const [userDrawer, setUserDrawer] = useState({ open: false, mode: 'create', user: null });
  const [userDrawerForm, setUserDrawerForm] = useState({
    username: '',
    password: '',
    display_name: '',
    role: 'operator',
  });
  const [userDrawerSaving, setUserDrawerSaving] = useState(false);
  const [deleteUserTarget, setDeleteUserTarget] = useState(null);
  const [deleteUserStep, setDeleteUserStep] = useState(1);
  const [deleteUserLoading, setDeleteUserLoading] = useState(false);

  const loadSettings = useCallback(async () => {
    if (!isAdmin) return;
    const data = await apiGet('/api/settings');
    if (data.items) setSettings(data.items);
  }, [isAdmin]);

  const loadUsers = useCallback(async () => {
    if (!isAdmin) return;
    setUsersLoading(true);
    setUsersMsg('');
    setUsersMsgErr(false);
    try {
      const data = await apiGet('/api/users');
      if (data.status !== 'success') {
        setUsersMsg(formatUserError(data.error) || '加载失败');
        setUsersMsgErr(true);
        return;
      }
      setUsers(data.items || []);
    } catch (err) {
      setUsersMsg(formatUserError(err.message) || '加载失败');
      setUsersMsgErr(true);
    } finally {
      setUsersLoading(false);
    }
  }, [isAdmin]);

  useEffect(() => {
    if (tab === 'system') loadSettings();
    if (tab === 'users') loadUsers();
  }, [tab, loadSettings, loadUsers]);

  const submitPassword = async (e) => {
    e.preventDefault();
    setPwdMsg('');
    if (pwd.next !== pwd.confirm) {
      setPwdMsg('两次输入的新密码不一致');
      return;
    }
    try {
      const data = await apiPost('/api/auth/password', {
        old_password: pwd.old,
        new_password: pwd.next,
      });
      if (data.status !== 'success') {
        setPwdMsg(formatUserError(data.error) || '修改失败');
        return;
      }
      setPwd({ old: '', next: '', confirm: '' });
      setPwdMsg('密码已更新');
    } catch (err) {
      setPwdMsg(formatUserError(err.message) || '修改失败');
    }
  };

  const saveSettings = async (e) => {
    e.preventDefault();
    setSettingsMsg('');
    try {
      const data = await apiPatch('/api/settings', settings);
      if (data.status !== 'success') {
        setSettingsMsg(formatUserError(data.error) || '保存失败');
        return;
      }
      if (data.items) setSettings(data.items);
      setSettingsMsg('已保存');
    } catch (err) {
      setSettingsMsg(formatUserError(err.message) || '保存失败');
    }
  };

  const setUsersFeedback = (msg, isErr = false) => {
    setUsersMsg(msg);
    setUsersMsgErr(isErr);
  };

  const emptyUserForm = () => ({
    username: '',
    password: '',
    display_name: '',
    role: 'operator',
  });

  const openUserDrawer = (mode, u = null) => {
    setUsersFeedback('');
    if (mode === 'create') {
      setUserDrawerForm(emptyUserForm());
      setUserDrawer({ open: true, mode: 'create', user: null });
      return;
    }
    setUserDrawerForm({
      username: u.username,
      display_name: u.display_name || u.username,
      role: u.role || 'operator',
      password: '',
    });
    setUserDrawer({ open: true, mode, user: u });
  };

  const closeUserDrawer = () => {
    setUserDrawer({ open: false, mode: 'create', user: null });
    setUserDrawerForm(emptyUserForm());
  };

  const onUserDrawerChange = (field, value) => {
    setUserDrawerForm((f) => ({ ...f, [field]: value }));
  };

  const saveUserDrawer = async () => {
    setUsersFeedback('');
    setUserDrawerSaving(true);
    try {
      if (userDrawer.mode === 'create') {
        const data = await apiPost('/api/users', userDrawerForm);
        if (data.status !== 'success') {
          setUsersFeedback(formatUserError(data.error) || '创建失败', true);
          return;
        }
        setUsersFeedback('用户已创建');
      } else if (userDrawer.mode === 'edit') {
        const body = {
          display_name: userDrawerForm.display_name,
          role: userDrawerForm.role,
        };
        if (userDrawerForm.password.trim()) {
          body.password = userDrawerForm.password.trim();
        }
        const data = await apiPatch(
          `/api/users/${encodeURIComponent(userDrawerForm.username)}`,
          body,
        );
        if (data.status !== 'success') {
          setUsersFeedback(formatUserError(data.error) || '保存失败', true);
          return;
        }
        setUsersFeedback('用户已更新');
      }
      closeUserDrawer();
      await loadUsers();
    } catch (err) {
      setUsersFeedback(formatUserError(err.message) || '保存失败', true);
    } finally {
      setUserDrawerSaving(false);
    }
  };

  const openDeleteUserConfirm = (username) => {
    window.setTimeout(() => {
      setDeleteUserTarget(username);
      setDeleteUserStep(1);
    }, 0);
  };

  const closeDeleteUserConfirm = () => {
    if (deleteUserLoading) return;
    setDeleteUserTarget(null);
    setDeleteUserStep(1);
  };

  const confirmDeleteUser = async () => {
    if (!deleteUserTarget) return;
    setDeleteUserLoading(true);
    setUsersFeedback('');
    const username = deleteUserTarget;
    try {
      const data = await apiDelete(`/api/users/${encodeURIComponent(username)}`);
      if (data.status !== 'success') {
        setUsersFeedback(formatUserError(data.error) || '删除失败', true);
        return;
      }
      if (userDrawer.user?.username === username) closeUserDrawer();
      setUsersFeedback('用户已删除');
      setDeleteUserTarget(null);
      setDeleteUserStep(1);
      await loadUsers();
    } catch (err) {
      setUsersFeedback(formatUserError(err.message) || '删除失败', true);
    } finally {
      setDeleteUserLoading(false);
    }
  };

  return (
    <div className="page settings-page">
        <h1 className="page-title">系统设置</h1>

        <div className="settings-tabs">
          {isAdmin && (
            <button type="button" className={tab === 'system' ? 'active' : ''} onClick={() => setTab('system')}>
              全局配置
            </button>
          )}
          <button type="button" className={tab === 'password' ? 'active' : ''} onClick={() => setTab('password')}>
            我的密码
          </button>
          {isAdmin && (
            <>
              <button type="button" className={tab === 'users' ? 'active' : ''} onClick={() => setTab('users')}>
                用户管理
              </button>
              <button type="button" className={tab === 'logs' ? 'active' : ''} onClick={() => setTab('logs')}>
                运行日志
              </button>
            </>
          )}
        </div>

        {tab === 'password' && (
          <form className="settings-panel" onSubmit={submitPassword}>
            <div className="settings-form-fields">
              <label>
                当前密码
                <input type="password" value={pwd.old} onChange={(e) => setPwd((p) => ({ ...p, old: e.target.value }))} required />
              </label>
              <label>
                新密码
                <input type="password" value={pwd.next} onChange={(e) => setPwd((p) => ({ ...p, next: e.target.value }))} required minLength={6} />
              </label>
              <label>
                确认新密码
                <input type="password" value={pwd.confirm} onChange={(e) => setPwd((p) => ({ ...p, confirm: e.target.value }))} required minLength={6} />
              </label>
            </div>
            <div className="settings-panel-footer">
              <button type="submit" className="settings-btn-primary">
                更新密码
              </button>
              {pwdMsg ? <p className={`settings-msg ${pwdMsg.includes('已') ? 'ok' : 'err'}`}>{pwdMsg}</p> : null}
            </div>
          </form>
        )}

        {tab === 'system' && isAdmin && (
          <form className="settings-panel" onSubmit={saveSettings}>
            <p className="settings-panel-lead">
              以下为<strong>全局默认值</strong>。未单独配置的摄像头将自动使用；在摄像头设置中可勾选「自定义」覆盖。
            </p>
            <div className="settings-form-fields">
              <InferenceModelGlobalFields
                backend={settings['models.backend']}
                det={settings['models.det']}
                onBackendChange={(value) =>
                  setSettings((s) => ({ ...s, 'models.backend': value }))
                }
                onDetChange={(value) => setSettings((s) => ({ ...s, 'models.det': value }))}
              />
              {CAMERA_OVERRIDE_FIELDS.map((field) => (
                <label key={field.key}>
                  <span className="settings-field-label">
                    {field.label}
                    <FieldHint text={settingsFieldTooltip(field)} />
                  </span>
                  {field.type === 'boolean' ? (
                    <span className="settings-toggle-field">
                      <span className="settings-toggle">
                        <input
                          type="checkbox"
                          checked={Boolean(settings[field.key])}
                          onChange={(e) =>
                            setSettings((s) => ({ ...s, [field.key]: e.target.checked }))
                          }
                        />
                        <span className="settings-toggle-track" aria-hidden="true" />
                      </span>
                    </span>
                  ) : field.type === 'select' ? (
                    <select
                      value={settings[field.key] ?? field.options[0]?.value ?? ''}
                      onChange={(e) =>
                        setSettings((s) => ({ ...s, [field.key]: e.target.value }))
                      }
                    >
                      {field.options.map((opt) => (
                        <option key={opt.value} value={opt.value}>
                          {opt.shortLabel || opt.label}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <input
                      type="number"
                      min={field.min}
                      max={field.max}
                      value={settings[field.key] ?? ''}
                      onChange={(e) =>
                        setSettings((s) => ({
                          ...s,
                          [field.key]: Number(e.target.value),
                        }))
                      }
                    />
                  )}
                </label>
              ))}
              <h3 className="settings-subsection-title">流水线日志</h3>
              <p className="settings-subsection-lead">
                各字段下方标注<strong>系统默认值</strong>；未配置或与默认相同时，后端按该值运行。
              </p>
              {GLOBAL_PIPELINE_LOG_FIELDS.map((field) => {
                const pipelineEnabled = Boolean(
                  resolveSettingValue(
                    settings,
                    GLOBAL_PIPELINE_LOG_FIELDS.find((f) => f.key === 'pipeline_log.enabled'),
                  ),
                );
                const disabled =
                  field.key === 'pipeline_log.sample' && !pipelineEnabled;
                const currentVal = resolveSettingValue(settings, field);
                return (
                  <label key={field.key} className={disabled ? 'settings-field-disabled' : undefined}>
                    <span className="settings-field-label">
                      {field.label}
                      <FieldHint text={settingsFieldTooltip(field)} />
                    </span>
                    {field.type === 'boolean' ? (
                      <span className="settings-toggle-field">
                        <span className="settings-toggle">
                          <input
                            type="checkbox"
                            checked={Boolean(currentVal)}
                            disabled={disabled}
                            onChange={(e) =>
                              setSettings((s) => ({ ...s, [field.key]: e.target.checked }))
                            }
                          />
                          <span className="settings-toggle-track" aria-hidden="true" />
                        </span>
                        <span className="settings-toggle-state">
                          {Boolean(currentVal) ? '开' : '关'}
                        </span>
                      </span>
                    ) : field.type === 'text' ? (
                      <input
                        type="text"
                        disabled={disabled}
                        placeholder={String(field.default ?? '')}
                        value={currentVal}
                        onChange={(e) =>
                          setSettings((s) => ({ ...s, [field.key]: e.target.value }))
                        }
                      />
                    ) : (
                      <input
                        type="number"
                        min={field.min}
                        max={field.max}
                        disabled={disabled}
                        placeholder={String(field.default ?? '')}
                        value={currentVal}
                        onChange={(e) =>
                          setSettings((s) => ({
                            ...s,
                            [field.key]: Number(e.target.value),
                          }))
                        }
                      />
                    )}
                    <span className="settings-field-default">
                      系统默认 <strong>{formatFieldDefaultValue(field)}</strong>
                    </span>
                  </label>
                );
              })}
              <h3 className="settings-subsection-title">碰撞告警</h3>
              {GLOBAL_COLLISION_FIELDS.map((field) => (
                <label key={field.key}>
                  <span className="settings-field-label">
                    {field.label}
                    <FieldHint text={settingsFieldTooltip(field)} />
                  </span>
                  <input
                    type="number"
                    min={field.min}
                    max={field.max}
                    value={settings[field.key] ?? ''}
                    onChange={(e) =>
                      setSettings((s) => ({
                        ...s,
                        [field.key]: Number(e.target.value),
                      }))
                    }
                  />
                </label>
              ))}
              <h3 className="settings-subsection-title">碰撞前置门控</h3>
              {GLOBAL_PREFILTER_FIELDS.map((field) => {
                const prefilterEnabled = Boolean(settings['collision_prefilter.enabled']);
                const disabled = field.key !== 'collision_prefilter.enabled' && !prefilterEnabled;
                return (
                  <label key={field.key} className={disabled ? 'settings-field-disabled' : undefined}>
                    <span className="settings-field-label">
                      {field.label}
                      <FieldHint text={settingsFieldTooltip(field)} />
                    </span>
                    {field.type === 'boolean' ? (
                      <span className="settings-toggle-field">
                        <span className="settings-toggle">
                          <input
                            type="checkbox"
                            checked={Boolean(settings[field.key])}
                            onChange={(e) =>
                              setSettings((s) => ({ ...s, [field.key]: e.target.checked }))
                            }
                          />
                          <span className="settings-toggle-track" aria-hidden="true" />
                        </span>
                      </span>
                    ) : (
                      <input
                        type="number"
                        min={field.min}
                        max={field.max}
                        step={field.step ?? 1}
                        disabled={disabled}
                        value={settings[field.key] ?? ''}
                        onChange={(e) =>
                          setSettings((s) => ({
                            ...s,
                            [field.key]: Number(e.target.value),
                          }))
                        }
                      />
                    )}
                  </label>
                );
              })}
            </div>
            <div className="settings-panel-footer">
              <button type="submit" className="settings-btn-primary">
                保存配置
              </button>
              {settingsMsg ? (
                <p className={`settings-msg ${settingsMsg.includes('已') ? 'ok' : 'err'}`}>{settingsMsg}</p>
              ) : null}
            </div>
          </form>
        )}

        {tab === 'users' && isAdmin && (
          <div className="settings-panel">
            <div className="settings-toolbar">
              <span className="settings-toolbar-summary">共 {users.length} 个用户</span>
              <div className="settings-toolbar-actions">
                <button type="button" className="settings-btn-secondary" disabled={usersLoading} onClick={loadUsers}>
                  {usersLoading ? '刷新中…' : '刷新列表'}
                </button>
                <button type="button" className="settings-btn-primary" onClick={() => openUserDrawer('create')}>
                  新建用户
                </button>
              </div>
            </div>

            {usersMsg ? (
              <p className={`settings-msg ${usersMsgErr ? 'err' : 'ok'}`}>{usersMsg}</p>
            ) : null}

            <table className="settings-table">
              <thead>
                <tr>
                  <th>用户名</th>
                  <th>显示名</th>
                  <th>角色</th>
                  <th className="settings-actions-col">操作</th>
                </tr>
              </thead>
              <tbody>
                {users.length === 0 && !usersLoading ? (
                  <tr>
                    <td colSpan={4} className="settings-cell-empty">
                      暂无用户
                    </td>
                  </tr>
                ) : null}
                {users.map((u) => {
                  const isSelf = u.username === user?.username;
                  return (
                    <tr key={u.username}>
                      <td className="settings-cell-mono">{u.username}</td>
                      <td>{u.display_name}</td>
                      <td>{u.role === 'admin' ? '管理员' : '操作员'}</td>
                      <td className="settings-actions-col">
                        <div className="settings-action-btns">
                          <button type="button" className="link-muted" onClick={() => openUserDrawer('view', u)}>
                            查看
                          </button>
                          <button type="button" className="link-primary" onClick={() => openUserDrawer('edit', u)}>
                            编辑
                          </button>
                          {!isSelf ? (
                            <button type="button" className="link-danger" onClick={() => openDeleteUserConfirm(u.username)}>
                              删除
                            </button>
                          ) : null}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>

            <UserDrawer
              open={userDrawer.open}
              mode={userDrawer.mode}
              user={userDrawer.user}
              form={userDrawerForm}
              onChange={onUserDrawerChange}
              onClose={closeUserDrawer}
              onSave={saveUserDrawer}
              onSwitchToEdit={() => openUserDrawer('edit', userDrawer.user)}
              saving={userDrawerSaving}
              isSelf={userDrawer.user?.username === user?.username}
            />
          </div>
        )}

        {tab === 'logs' && isAdmin && <LogsPanel />}

        <ConfirmDialog
          open={Boolean(deleteUserTarget)}
          step={deleteUserStep}
          title="删除用户"
          message={
            deleteUserStep === 1
              ? `确定删除用户「${deleteUserTarget}」？`
              : '删除后无法恢复，请再次确认是否继续。'
          }
          onCancel={closeDeleteUserConfirm}
          onContinue={() => setDeleteUserStep(2)}
          onConfirm={confirmDeleteUser}
          loading={deleteUserLoading}
        />
    </div>
  );
}
