"""
Reaper OSC client — sends transport commands to Reaper via UDP.

Reaper's OSC is enabled in: Preferences -> Control/OSC/web -> Add -> OSC.
The user sets:
  - Mode: "Local port" (Reaper listens)
  - Local listen port: must match settings.reaper.port (default 8000)
  - Allow binding messages: checked

OSC messages used:
  /play        — toggle play (1 = play)
  /stop        — stop transport (1 = stop)
  /pause       — toggle pause (1 = pause)
  /record      — toggle record (1 = record)
  /time        — seek to position in seconds (float)

python-osc sends fire-and-forget UDP, so there's no connection state.
If Reaper isn't listening, the messages just vanish (which is fine — the
user sees "lockstep enabled" in settings and knows Reaper needs to be open).
"""
from __future__ import annotations

from typing import Optional

from core.events import Events, event_bus
from core.interfaces import IReaperClient
from services.settings_service import SettingsService

try:
    from pythonosc.udp_client import SimpleUDPClient

    OSC_AVAILABLE = True
except ImportError:
    OSC_AVAILABLE = False


class ReaperService(IReaperClient):
    def __init__(self, settings: SettingsService) -> None:
        self._settings = settings
        self._client: Optional[SimpleUDPClient] = None
        self._ensure_client()

    def _ensure_client(self) -> None:
        """(Re-)create the UDP client from current settings."""
        if not OSC_AVAILABLE:
            print("[reaper] python-osc not installed — skipping")
            return
        s = self._settings.get().reaper
        if s.host and s.port:
            try:
                self._client = SimpleUDPClient(s.host, s.port)
                print(f"[reaper] OSC client ready → {s.host}:{s.port}")
            except Exception as e:
                print(f"[reaper] failed to create OSC client: {e}")
                self._client = None

    def is_configured(self) -> bool:
        s = self._settings.get().reaper
        return bool(s.host and s.port)

    def _send(self, address: str, value) -> None:
        """Send an OSC message, silently ignoring failures."""
        if self._client is None:
            self._ensure_client()
        if self._client is None:
            return
        try:
            self._client.send_message(address, value)
        except Exception as e:
            print(f"[reaper] OSC send {address} failed: {e}")

    def play(self) -> None:
        self._send("/play", 1)

    def stop(self) -> None:
        self._send("/stop", 1)

    def pause(self) -> None:
        self._send("/pause", 1)

    def record(self) -> None:
        self._send("/record", 1)

    def seek(self, position_seconds: float) -> None:
        self._send("/time", float(position_seconds))

    def refresh_connection(self) -> None:
        """Call after settings change to pick up new host/port."""
        self._ensure_client()
        event_bus.publish_sync(
            Events.REAPER_STATUS_CHANGED,
            {"configured": self.is_configured()},
        )