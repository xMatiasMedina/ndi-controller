"""
OBS WebSocket client — connects to OBS via obsws-python v5.

Features:
  - Async connect/disconnect with auto-retry on failure
  - Scene listing and switching (the feature PlayerService needs)
  - Connection status tracking + event bus notifications
  - Auto-provision of StreamScreen scene + NDI source on connect
  - Auto-switch to StreamScreen on screen share, revert on stop
  - Graceful handling of OBS not running or refusing connections

Requires OBS 28+ with WebSocket Server enabled:
  Tools -> obs-websocket Settings -> Enable WebSocket server
"""
from __future__ import annotations

import asyncio
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

        # Scene that was active before we switched to StreamScreen
        self._previous_scene: Optional[str] = None
        # Pending revert timer handle
        self._revert_task: Optional[asyncio.TimerHandle] = None

    # ---------------------------------------------------------------- connect

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

            # Auto-provision the screen share scene + source
            await self._ensure_stream_screen()

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
        self._previous_scene = None
        event_bus.publish_sync(Events.OBS_STATUS_CHANGED, {"connected": False})

    def is_connected(self) -> bool:
        return self._connected

    # ---------------------------------------------------------------- scenes

    async def list_scenes(self) -> List[str]:
        """Return scene names in the order OBS reports them."""
        if not self._connected or self._client is None:
            return []
        loop = asyncio.get_running_loop()
        try:
            resp = await loop.run_in_executor(
                None, self._client.get_scene_list
            )
            return [s["sceneName"] for s in resp.scenes]
        except Exception as e:
            print(f"[obs] list_scenes failed: {e}")
            self._mark_disconnected(e)
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
            self._mark_disconnected(e)
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
            self._mark_disconnected(e)
            return False

    # ---------------------------------------------------------------- sources

    async def list_scene_items(self, scene_name: str) -> List[dict]:
        """Return sources (scene items) for a given scene."""
        if not self._connected or self._client is None:
            return []
        loop = asyncio.get_running_loop()
        try:
            resp = await loop.run_in_executor(
                None, self._client.get_scene_item_list, scene_name
            )
            return [
                {
                    "sceneItemId": item["sceneItemId"],
                    "sourceName": item.get("sourceName", ""),
                    "inputKind": item.get("inputKind"),
                    "sceneItemEnabled": item.get("sceneItemEnabled", True),
                }
                for item in resp.scene_items
            ]
        except Exception as e:
            print(f"[obs] list_scene_items('{scene_name}') failed: {e}")
            self._mark_disconnected(e)
            return []

    async def set_scene_item_enabled(
        self, scene_name: str, item_id: int, enabled: bool
    ) -> bool:
        """Show/hide a source (scene item) within a scene."""
        if not self._connected or self._client is None:
            return False
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                None,
                self._client.set_scene_item_enabled,
                scene_name,
                item_id,
                enabled,
            )
            print(f"[obs] set item {item_id} in '{scene_name}' enabled={enabled}")
            return True
        except Exception as e:
            print(f"[obs] set_scene_item_enabled failed: {e}")
            self._mark_disconnected(e)
            return False

    # -------------------------------------------------------- stream screen

    async def _ensure_stream_screen(self) -> None:
        """Make sure the StreamScreen scene + NDI source exist in OBS."""
        import dev_config
        cfg = dev_config.get().screen_share_obs

        if not self._connected or self._client is None:
            return

        loop = asyncio.get_running_loop()

        try:
            scenes = await self.list_scenes()
            if cfg.scene_name in scenes:
                # Scene already exists (user-configured) — use it as-is,
                # don't touch its sources.
                return

            # Only auto-provision a fresh scene + NDI source when nothing exists.
            await loop.run_in_executor(
                None, self._client.create_scene, cfg.scene_name
            )
            await loop.run_in_executor(
                None,
                self._client.create_input,
                cfg.scene_name,        # sceneName
                cfg.source_name,       # inputName
                cfg.ndi_input_kind,    # inputKind (e.g. "ndi_source")
                {"ndi_source_name": cfg.ndi_source_name},  # inputSettings
                True,                  # sceneItemEnabled
            )
            print(
                f"[obs] provisioned scene '{cfg.scene_name}' with NDI source "
                f"'{cfg.source_name}' (from '{cfg.ndi_source_name}')"
            )
        except Exception as e:
            # Non-fatal — screen share still works, just without auto-provision.
            print(f"[obs] WARNING: failed to provision {cfg.scene_name}: {e}")

    async def switch_to_stream_screen(self) -> None:
        """Switch OBS to the StreamScreen scene, remembering the previous one."""
        import dev_config
        cfg = dev_config.get().screen_share_obs

        # Cancel any pending revert
        if self._revert_task is not None:
            self._revert_task.cancel()
            self._revert_task = None

        current = await self.get_current_scene()
        if current and current != cfg.scene_name:
            self._previous_scene = current

        await self.set_current_scene(cfg.scene_name)
        # The ShareScreen scene's layout — one NDI copy fit into each physical
        # screen's region (e.g. Warehouse / Office), matching the LED-wall
        # mapping — is configured in OBS by the operator. We deliberately do NOT
        # reposition or resize its sources here; doing so would clobber that
        # per-screen calibration and split the image across the screens.
        print(f"[obs] switched to {cfg.scene_name} (previous: {self._previous_scene})")

    async def schedule_revert_scene(self) -> None:
        """After screen share ends, wait then revert to the previous scene.

        The delay allows for brief reconnects — if a new share starts
        within the window, switch_to_stream_screen cancels the revert.
        """
        import dev_config
        cfg = dev_config.get().screen_share_obs

        if self._previous_scene is None:
            return

        previous = self._previous_scene

        async def _do_revert():
            await asyncio.sleep(cfg.switch_delay_sec)
            # Only revert if we're still on StreamScreen and no new share started
            current = await self.get_current_scene()
            if current == cfg.scene_name:
                await self.set_current_scene(previous)
                print(f"[obs] reverted to scene '{previous}'")
            self._previous_scene = None
            self._revert_task = None

        # Schedule the revert as an asyncio task
        self._revert_task = asyncio.ensure_future(_do_revert())

    # ---------------------------------------------------------------- internal

    def _mark_disconnected(self, error: Exception) -> None:
        """Mark as disconnected on any communication failure.
        The next explicit connect() call will re-establish."""
        self._connected = False
        self._client = None
        event_bus.publish_sync(
            Events.OBS_STATUS_CHANGED,
            {"connected": False, "error": str(error)},
        )
