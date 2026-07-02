"""Constants for the LoreaTec NDI Controller integration."""
from __future__ import annotations

DOMAIN = "loreatec_ndi"

DEFAULT_PORT = 8000
SCAN_INTERVAL_SECONDS = 30

# Stored in the config entry: the resolved base URL (scheme auto-detected).
CONF_BASE_URL = "base_url"

# Sentinel option shown in the playlist combo box to clear the default.
NONE_OPTION = "— None —"
