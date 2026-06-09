"""未登录时拦截 API / WebSocket。"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from core.auth_admin import enrich_session_user
from services.auth_service import get_session_user

PUBLIC_PREFIXES = (
    "/api/version",
    "/api/auth/",
    "/api/edge/",
    "/assets/",
)


def _is_public(path: str) -> bool:
    if path in ("/login", "/favicon.ico"):
        return True
    return any(path.startswith(p) for p in PUBLIC_PREFIXES)


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, settings_getter):
        super().__init__(app)
        self._settings_getter = settings_getter

    async def dispatch(self, request: Request, call_next):
        settings = self._settings_getter()
        if not settings.get("enabled"):
            request.state.user = {"username": "guest", "display_name": "访客"}
            return await call_next(request)

        path = request.url.path
        if _is_public(path):
            return await call_next(request)

        user = get_session_user(request, settings)
        if not user:
            if path.startswith("/api/") or path.startswith("/ws/"):
                return JSONResponse(
                    status_code=401,
                    content={"status": "error", "error": "未登录或会话已过期", "code": "unauthorized"},
                )
        else:
            users_file = settings.get("local", {}).get("users_file", "localdata/auth_users.json")
            request.state.user = enrich_session_user(user, users_file)
        return await call_next(request)
