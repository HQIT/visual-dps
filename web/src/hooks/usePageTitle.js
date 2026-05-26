import { useEffect } from 'react';
import { useLocation } from 'react-router-dom';

export const APP_NAME = 'DiDPS';

const ROUTE_TITLES = {
  '/login': '登录',
  '/': '摄像头总览',
  '/matrix': '事件矩阵',
  '/services': '服务总览',
  '/monitor': '检测监控',
  '/settings': '系统设置',
};

export function usePageTitle() {
  const { pathname, search } = useLocation();

  useEffect(() => {
    const page = ROUTE_TITLES[pathname];
    if (pathname === '/monitor' && search.includes('camera=')) {
      document.title = `检测监控 - ${APP_NAME}`;
      return;
    }
    document.title = page ? `${page} - ${APP_NAME}` : APP_NAME;
  }, [pathname, search]);
}
