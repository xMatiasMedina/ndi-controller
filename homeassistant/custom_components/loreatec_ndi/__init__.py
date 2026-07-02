"""The LoreaTec NDI Controller integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import NdiControllerApi
from .const import CONF_BASE_URL, DOMAIN
from .coordinator import NdiCoordinator

PLATFORMS: list[Platform] = [Platform.SELECT]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up from a config entry."""
    # The station uses a self-signed cert, so don't verify SSL (LAN only).
    session = async_get_clientsession(hass, verify_ssl=False)
    api = NdiControllerApi(session, entry.data[CONF_BASE_URL])

    coordinator = NdiCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        # .get(...).pop(..., None) so removal can't KeyError when setup never
        # finished (e.g. the controller was unreachable on first connect).
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unloaded
