"""Config flow for the LoreaTec NDI Controller integration."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import NdiApiError, NdiControllerApi
from .const import CONF_BASE_URL, DEFAULT_PORT, DOMAIN


async def _detect_base_url(hass, host: str, port: int) -> str | None:
    """Find a reachable base URL. The station serves HTTPS (self-signed); some
    dev boxes serve plain HTTP — try HTTPS first, then HTTP."""
    session = async_get_clientsession(hass, verify_ssl=False)
    for scheme in ("https", "http"):
        base = f"{scheme}://{host}:{port}"
        try:
            if await NdiControllerApi(session, base).health():
                return base
        except NdiApiError:
            continue
    return None


class NdiConfigFlow(ConfigFlow, domain=DOMAIN):
    """Ask for the IP and port, then verify the controller is reachable."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = user_input[CONF_PORT]

            await self.async_set_unique_id(f"{host}:{port}")
            self._abort_if_unique_id_configured()

            base_url = await _detect_base_url(self.hass, host, port)
            if base_url is None:
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(
                    title=f"NDI Controller ({host})",
                    data={
                        CONF_HOST: host,
                        CONF_PORT: port,
                        CONF_BASE_URL: base_url,
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST): cv.string,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): cv.port,
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors
        )
