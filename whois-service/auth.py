"""Request authentication: API key for /whois, Authentik headers for /admin."""

from __future__ import annotations

import hmac

from fastapi import Depends, Header, HTTPException, Request, status

from config import Settings, get_settings


def require_api_key(
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    presented = x_api_key
    if not presented and authorization and authorization.lower().startswith("bearer "):
        presented = authorization[7:].strip()
    if not settings.whois_api_key:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "WHOIS_API_KEY is not configured")
    if not presented or not any(
        hmac.compare_digest(presented.encode(), key.encode()) for key in settings.whois_api_key
    ):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Invalid API key", headers={"WWW-Authenticate": "Bearer"}
        )


def require_admin(request: Request, settings: Settings = Depends(get_settings)) -> str:
    """Returns the Authentik uid. Fails closed when no admin group is configured."""
    if settings.admin_proxy_shared_secret:
        presented = request.headers.get(settings.admin_proxy_shared_secret_header, "")
        if not hmac.compare_digest(presented, settings.admin_proxy_shared_secret):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Request bypassed the proxy")
    identity = request.headers.get(settings.admin_identity_header, "").strip()
    if not identity:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated via Authentik")
    if not settings.admin_allowed_groups:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "ADMIN_ALLOWED_GROUPS not set")
    raw = request.headers.get(settings.admin_groups_header, "")
    groups = {g.strip() for g in raw.split(settings.admin_groups_separator) if g.strip()}
    if not groups & set(settings.admin_allowed_groups):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not in an admin group")
    return identity
