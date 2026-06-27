import { Link, NavLink, useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import logoUrl from '../assets/logo.svg?url';
import './AppNav.css';

export default function AppNav() {
  const { user, authRequired, logout } = useAuth();
  const navigate = useNavigate();

  const handleLogout = async () => {
    await logout();
    navigate('/login', { replace: true });
  };

  return (
    <nav className="app-nav">
      <div className="app-nav-start">
        <Link to="/" className="app-nav-brand" title="DiDPS">
          <img src={logoUrl} alt="DiDPS" className="app-nav-logo" width={138} height={32} />
        </Link>
        <div className="app-nav-links">
        <NavLink to="/" end>
          摄像头总览
        </NavLink>
        <NavLink to="/matrix">
          事件矩阵
        </NavLink>
        <NavLink to="/topology">
          服务拓扑
        </NavLink>
        <NavLink to="/bench">
          离线评测
        </NavLink>
        </div>
      </div>
      {authRequired && user && (
        <div className="app-nav-user">
          <Link to="/settings" className="app-nav-settings" title="系统设置">
            ⚙
          </Link>
          <span className="app-nav-name" title={user.username}>
            {user.display_name || user.username}
          </span>
          <button type="button" className="app-nav-logout" onClick={handleLogout}>
            退出
          </button>
        </div>
      )}
    </nav>
  );
}
