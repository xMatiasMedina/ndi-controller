"""Centralised paths and defaults. Keep static config out of the services."""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

MEDIA_DIR = DATA_DIR / "media"
MEDIA_DIR.mkdir(exist_ok=True)

LIBRARY_FILE = DATA_DIR / "library.json"
PLAYLISTS_FILE = DATA_DIR / "playlists.json"
SETTINGS_FILE = DATA_DIR / "settings.json"

# NDI defaults — overridable in settings
DEFAULT_NDI_VIDEO_NAME = "AV_Platform_NDI"
DEFAULT_NDI_AUDIO_NAME = "AV_Platform_NDI_Audio"

# Audio constants — NDI's preferred format
AUDIO_SAMPLE_RATE = 48000
AUDIO_CHANNELS = 2

# Default integration endpoints
DEFAULT_OBS_HOST = "127.0.0.1"
DEFAULT_OBS_PORT = 4455
DEFAULT_OBS_PASSWORD = ""

DEFAULT_REAPER_HOST = "127.0.0.1"
DEFAULT_REAPER_PORT = 8000  # Reaper's default OSC send port; user will likely change