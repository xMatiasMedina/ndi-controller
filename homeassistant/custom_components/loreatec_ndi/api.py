"""Thin async client for the LoreaTec NDI Controller HTTP API."""
from __future__ import annotations

import asyncio

from aiohttp import ClientError, ClientSession, ClientTimeout

_TIMEOUT = ClientTimeout(total=10)


class NdiApiError(Exception):
    """Raised when a call to the NDI Controller API fails."""


class NdiControllerApi:
    """Wraps the handful of endpoints the integration needs.

    The station serves HTTPS with a self-signed certificate, so the aiohttp
    session must be created with ``verify_ssl=False`` (see __init__.py / the
    config flow). HTTP works through the same session too.
    """

    def __init__(self, session: ClientSession, base_url: str) -> None:
        self._session = session
        self._base = base_url.rstrip("/")

    @property
    def base_url(self) -> str:
        return self._base

    async def _get(self, path: str):
        try:
            async with self._session.get(self._base + path, timeout=_TIMEOUT) as resp:
                resp.raise_for_status()
                return await resp.json()
        except (ClientError, asyncio.TimeoutError) as err:
            raise NdiApiError(f"GET {path} failed: {err}") from err

    async def _post(self, path: str, payload: dict):
        try:
            async with self._session.post(
                self._base + path, json=payload, timeout=_TIMEOUT
            ) as resp:
                resp.raise_for_status()
                return await resp.json()
        except (ClientError, asyncio.TimeoutError) as err:
            raise NdiApiError(f"POST {path} failed: {err}") from err

    async def health(self) -> bool:
        """True if the controller answers and reports healthy."""
        data = await self._get("/api/health")
        return isinstance(data, dict) and data.get("status") == "ok"

    async def get_playlists(self) -> list[dict]:
        """List of {id, name, video_ids}."""
        return await self._get("/api/playlists")

    async def get_default_playlist_id(self) -> str | None:
        settings = await self._get("/api/settings")
        return settings.get("default_playlist_id")

    async def set_default_playlist(self, playlist_id: str | None) -> None:
        """Set (or clear, with None) the auto-resuming default playlist.

        The controller applies it immediately: starts it if idle, or swaps if
        the previous default was showing.
        """
        await self._post("/api/default-playlist", {"playlist_id": playlist_id})
