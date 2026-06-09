"""Edge REST ingest 配置（Keycloak JWT，与 UI Session OAuth 分离）。"""

from __future__ import annotations

import os
from typing import Any

from core.auth_settings import _env_bool, _load_json_file

DEFAULT_EDGE_CONFIG_FILE = "localdata/edge_api_config.json"


def load_edge_settings(app_config: dict | None = None) -> dict[str, Any]:
    file_cfg: dict = {}
    if app_config and isinstance(app_config.get("edge_api"), dict):
        file_cfg = dict(app_config["edge_api"])
    else:
        path = os.environ.get("EDGE_CONFIG_FILE", DEFAULT_EDGE_CONFIG_FILE)
        file_cfg = _load_json_file(path)

    enabled = _env_bool("EDGE_API_ENABLED", bool(file_cfg.get("enabled", False)))
    issuer = os.environ.get("EDGE_JWT_ISSUER", "").strip() or str(file_cfg.get("issuer") or "").strip()
    audience = os.environ.get("EDGE_JWT_AUDIENCE", "").strip() or str(file_cfg.get("audience") or "").strip()
    jwks_url = (
        os.environ.get("EDGE_JWT_JWKS_URL", "").strip()
        or str(file_cfg.get("jwks_url") or "").strip()
    )
    if not jwks_url and issuer:
        base = issuer.rstrip("/")
        jwks_url = f"{base}/protocol/openid-connect/certs"

    if enabled and (not issuer or not jwks_url):
        enabled = False

    return {
        "enabled": enabled,
        "issuer": issuer,
        "audience": audience,
        "jwks_url": jwks_url,
    }
