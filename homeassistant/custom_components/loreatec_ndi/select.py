"""Select entity to choose the NDI Controller's default playlist."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, NONE_OPTION
from .coordinator import NdiCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: NdiCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([DefaultPlaylistSelect(coordinator, entry)])


class DefaultPlaylistSelect(CoordinatorEntity[NdiCoordinator], SelectEntity):
    """A combo box of every playlist; selecting one sets the default."""

    _attr_has_entity_name = True
    _attr_name = "Default playlist"
    _attr_icon = "mdi:playlist-play"

    def __init__(self, coordinator: NdiCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_default_playlist"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="NDI Controller",
            manufacturer="LoreaTec",
            model="NDI Controller",
            configuration_url=coordinator.api.base_url,
        )

    @property
    def _playlists(self) -> list[dict]:
        data = self.coordinator.data or {}
        return data.get("playlists") or []

    @property
    def options(self) -> list[str]:
        # Sentinel first so the default can be cleared from the dropdown.
        return [NONE_OPTION] + [p["name"] for p in self._playlists]

    @property
    def current_option(self) -> str:
        data = self.coordinator.data or {}
        default_id = data.get("default_id")
        if default_id:
            for p in self._playlists:
                if p.get("id") == default_id:
                    return p["name"]
        return NONE_OPTION

    async def async_select_option(self, option: str) -> None:
        playlist_id: str | None = None
        if option != NONE_OPTION:
            playlist_id = next(
                (p["id"] for p in self._playlists if p.get("name") == option),
                None,
            )
        await self.coordinator.api.set_default_playlist(playlist_id)
        await self.coordinator.async_request_refresh()
