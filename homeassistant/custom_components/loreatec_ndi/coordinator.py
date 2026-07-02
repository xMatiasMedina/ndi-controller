"""Polls the NDI Controller for playlists and the current default."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import NdiApiError, NdiControllerApi
from .const import DOMAIN, SCAN_INTERVAL_SECONDS

_LOGGER = logging.getLogger(__name__)


class NdiCoordinator(DataUpdateCoordinator[dict]):
    """Fetches {playlists, default_id} on an interval and on demand."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, api: NdiControllerApi
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=SCAN_INTERVAL_SECONDS),
        )
        self.api = api
        self.entry = entry

    async def _async_update_data(self) -> dict:
        try:
            playlists = await self.api.get_playlists()
            default_id = await self.api.get_default_playlist_id()
        except NdiApiError as err:
            raise UpdateFailed(str(err)) from err
        return {"playlists": playlists, "default_id": default_id}
