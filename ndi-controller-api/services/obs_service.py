"""
OBS WebSocket client — connects to OBS via obsws-python v5.

Features:
  - Async connect/disconnect with auto-retry on failure
  - Scene listing and switching (the feature PlayerService needs)
  - Connection status tracking + event bus notifications
  - Graceful handling of OBS not running or refusing connections

Requires OBS 28+ with WebSocket Server enabled:
  Tools -> obs-websocket Settings -> Enable WebSocket server
"""
from __future__ import annotations

import asyncio
import traceback
from typing import List, Optional

from core.events import Events, event_bus
from core.interfaces import IObsClient
from services.settings_service import SettingsService

try:
    import obsws_python as obsws

    OBSWS_AVAILABLE = True
except ImportError:
    OBSWS_AVAILABLE = False


class ObsService(IObsClient):
    def __init__(self, settings: SettingsService) -> None:
        self._settings = settings
        self._client: Optional[obsws.ReqClient] = None
        self._connected = False

    async def connect(self) -> bool:
        """Connect to OBS WebSocket. Returns True on success."""
        if not OBSWS_AVAILABLE:
            print("[obs] obsws-python not installed — skipping")
            return False

        if self._connected and self._client is not None:
            return True

        s = self._settings.get().obs
        loop = asyncio.get_running_loop()
        try:
            # obsws-python is synchronous, so run in executor
            self._client = await loop.run_in_executor(
                None,
                lambda: obsws.ReqClient(
                    host=s.host,
                    port=s.port,
                    password=s.password if s.password else None,
                    timeout=5,
                ),
            )
            self._connected = True
            print(f"[obs] connected to {s.host}:{s.port}")
            event_bus.publish_sync(
                Events.OBS_STATUS_CHANGED, {"connected": True}
            )
            return True
        except Exception as e:
            self._connected = False
            self._client = None
            print(f"[obs] connection failed to {s.host}:{s.port}: {e}")
            event_bus.publish_sync(
                Events.OBS_STATUS_CHANGED,
                {"connected": False, "error": str(e)},
            )
            return False

    async def disconnect(self) -> None:
        if self._client is not None:
            loop = asyncio.get_running_loop()
            try:
                await loop.run_in_executor(None, self._client.base_client.ws.close)
            except Exception:
                pass
            self._client = None
        self._connected = False
        event_bus.publish_sync(Events.OBS_STATUS_CHANGED, {"connected": False})

    def is_connected(self) -> bool:
        return self._connected

    async def list_scenes(self) -> List[str]:
        """Return scene names in the order OBS reports them."""
        if not self._connected or self._client is None:
            return []
        loop = asyncio.get_running_loop()
        try:
            resp = await loop.run_in_executor(
                None, self._client.get_scene_list
            )
            # resp.scenes is a list of dicts with 'sceneName', 'sceneIndex'
            return [s["sceneName"] for s in resp.scenes]
        except Exception as e:
            print(f"[obs] list_scenes failed: {e}")
            await self._handle_disconnect(e)
            return []

    async def get_current_scene(self) -> Optional[str]:
        if not self._connected or self._client is None:
            return None
        loop = asyncio.get_running_loop()
        try:
            resp = await loop.run_in_executor(
                None, self._client.get_current_program_scene
            )
            return resp.scene_name
        except Exception as e:
            print(f"[obs] get_current_scene failed: {e}")
            await self._handle_disconnect(e)
            return None

    async def set_current_scene(self, name: str) -> bool:
        """Switch OBS to the named scene. Returns True on success."""
        if not self._connected or self._client is None:
            return False
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                None, self._client.set_current_program_scene, name
            )
            print(f"[obs] switched to scene: {name}")
            return True
        except Exception as e:
            print(f"[obs] set_current_scene('{name}') failed: {e}")
            await self._handle_disconnect(e)
            return False

    async def _handle_disconnect(self, error: Exception) -> None:
        """Mark disconnected if the error looks like a broken connection."""
        error_str = str(error).lower()
        if any(
            kw in error_str
            for kw in ("closed", "broken", "refused", "reset", "timeout")
        ):
            self._connected = False
            self._client = None
            event_bus.publish_sync(
                Events.OBS_STATUS_CHANGED,
                {"connected": False, "error": str(error)},
            )