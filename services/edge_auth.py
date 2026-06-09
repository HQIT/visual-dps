"""Edge API Bearer JWT 校验（Keycloak Client Credentials）。"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

_jwk_client = None
_jwk_client_url = ""


def _get_jwk_client(jwks_url: str):
    global _jwk_client, _jwk_client_url
    if _jwk_client is not None and _jwk_client_url == jwks_url:
        return _jwk_client
    try:
        from jwt import PyJWKClient
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="Edge JWT 依赖未安装（PyJWT）") from exc
    _jwk_client = PyJWKClient(jwks_url, cache_keys=True, lifespan=3600)
    _jwk_client_url = jwks_url
    return _jwk_client


def _bearer_token(request: Request) -> str:
    auth = str(request.headers.get("authorization") or "").strip()
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="缺少 Bearer Token")
    token = auth[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Bearer Token 为空")
    return token


def verify_edge_bearer(request: Request, settings: dict[str, Any]) -> dict[str, Any]:
    if not settings.get("enabled"):
        raise HTTPException(status_code=503, detail="Edge ingest API 未启用")

    token = _bearer_token(request)
    jwks_url = str(settings.get("jwks_url") or "").strip()
    issuer = str(settings.get("issuer") or "").strip()
    if not jwks_url or not issuer:
        raise HTTPException(status_code=503, detail="Edge JWT 配置不完整")

    try:
        import jwt
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="Edge JWT 依赖未安装（PyJWT）") from exc

    try:
        signing_key = _get_jwk_client(jwks_url).get_signing_key_from_jwt(token)
        decode_kwargs: dict[str, Any] = {
            "algorithms": ["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"],
            "issuer": issuer,
            "options": {"require": ["exp", "iss"]},
        }
        audience = str(settings.get("audience") or "").strip()
        if audience:
            decode_kwargs["audience"] = audience
        else:
            decode_kwargs["options"]["verify_aud"] = False
        return jwt.decode(token, signing_key.key, **decode_kwargs)
    except HTTPException:
        raise
    except Exception as exc:
        logger.info("Edge JWT verify failed: %s", exc)
        raise HTTPException(status_code=401, detail="无效或过期的 Access Token") from exc
