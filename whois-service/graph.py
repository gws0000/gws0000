"""Minimal synchronous Microsoft Graph client (client-credentials)."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from typing import Any

import httpx

from config import Settings

log = logging.getLogger(__name__)

_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
_MAX_ATTEMPTS = 5


class GraphError(RuntimeError):
    pass


class DeltaExpired(GraphError):
    """The stored deltaLink is no longer valid; a full resync is required."""


class GraphClient:
    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self._s = settings
        self._client = client or httpx.Client(timeout=settings.graph_timeout_seconds)
        self._token: str | None = None
        self._token_expires_at = 0.0

    def close(self) -> None:
        self._client.close()

    def _access_token(self) -> str:
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token
        if not self._s.graph_configured:
            raise GraphError("AZURE_TENANT_ID / AZURE_CLIENT_ID / AZURE_CLIENT_SECRET not set")
        resp = self._client.post(
            f"{self._s.graph_authority.rstrip('/')}/{self._s.azure_tenant_id}/oauth2/v2.0/token",
            data={
                "client_id": self._s.azure_client_id,
                "client_secret": self._s.azure_client_secret,
                "scope": "https://graph.microsoft.com/.default",
                "grant_type": "client_credentials",
            },
        )
        if resp.status_code >= 400:
            raise GraphError(f"token request failed: {resp.status_code} {resp.text[:300]}")
        payload = resp.json()
        self._token = payload["access_token"]
        self._token_expires_at = time.monotonic() + max(
            int(payload.get("expires_in", 3600)) - 60, 30
        )
        return self._token

    def get(self, url: str) -> dict[str, Any]:
        if url.startswith("/"):
            url = f"{self._s.graph_base_url.rstrip('/')}{url}"
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                resp = self._client.get(
                    url, headers={"Authorization": f"Bearer {self._access_token()}"}
                )
            except httpx.TransportError as exc:
                if attempt == _MAX_ATTEMPTS:
                    raise GraphError(f"Graph unreachable: {exc}") from exc
                time.sleep(_backoff(attempt))
                continue
            if resp.status_code == 410:
                raise DeltaExpired(resp.text[:300])
            if resp.status_code in _RETRY_STATUSES and attempt < _MAX_ATTEMPTS:
                delay = _retry_after(resp) or _backoff(attempt)
                log.warning("graph %s, retrying in %.1fs", resp.status_code, delay)
                time.sleep(delay)
                continue
            if resp.status_code >= 400:
                raise GraphError(f"GET {url} -> {resp.status_code} {resp.text[:300]}")
            return resp.json()
        raise AssertionError("unreachable")

    def iter_delta(self, url: str) -> Iterator[tuple[list[dict[str, Any]], str | None]]:
        """Yield (items, delta_link) per page; delta_link is set only on the last page."""
        while url:
            page = self.get(url)
            delta_link = page.get("@odata.deltaLink")
            yield page.get("value", []), delta_link
            url = page.get("@odata.nextLink")


def _backoff(attempt: int) -> float:
    return min(2.0**attempt, 60.0)


def _retry_after(resp: httpx.Response) -> float | None:
    try:
        return float(resp.headers["Retry-After"])
    except (KeyError, ValueError):
        return None
