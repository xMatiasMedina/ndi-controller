"""
WebSocket routes:

1. /ws/state — broadcasts player/OBS/Reaper state changes to the dashboard.
   The route subscribes to the event bus and forwards each event as a JSON
   message of the form: {"event": "...", "payload": {...}}.

2. /ws/screen-share — receives JPEG frames from the browser's getDisplayMedia
   capture and pushes them into the active BrowserSource via PlayerService.
   On connect: switches OBS to the StreamScreen scene.
   On disconnect: schedules a delayed revert to the previous scene.
   Binary messages = JPEG frame data. Text messages = JSON control commands.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from core.events import Events, event_bus
from services.player_service import PlayerService
from services.obs_service import ObsService

router = APIRouter()


# ----- Dependency placeholders (set by main.py) -----
_player: PlayerService | None = None
_obs: ObsService | None = None


def set_player_ref(player: PlayerService) -> None:
    global _player
    _player = player


def set_obs_ref(obs: ObsService) -> None:
    global _obs
    _obs = obs


# ----- Outbound: state broadcast -----
@router.websocket("/ws/state")
async def state_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    queue: asyncio.Queue = asyncio.Queue(maxsize=64)

    async def handler(event: str, payload: Any) -> None:
        try:
            queue.put_nowait({"event": event, "payload": payload})
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                queue.put_nowait({"event": event, "payload": payload})
            except asyncio.QueueFull:
                pass

    events_to_forward = [
        Events.PLAYER_STATE_CHANGED,
        Events.POSITION_CHANGED,
        Events.OBS_STATUS_CHANGED,
        Events.REAPER_STATUS_CHANGED,
        Events.NDI_STATUS_CHANGED,
    ]
    for ev in events_to_forward:
        event_bus.subscribe(ev, handler)

    try:
        while True:
            msg = await queue.get()
            await websocket.send_text(json.dumps(msg, default=str))
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        for ev in events_to_forward:
            event_bus.unsubscribe(ev, handler)


# ----- Inbound: browser screen share -----
@router.websocket("/ws/screen-share")
async def screen_share_socket(websocket: WebSocket) -> None:
    await websocket.accept()

    if _player is None:
        await websocket.close(code=1011, reason="Player not initialized")
        return

    if not _player.is_browser_active:
        await websocket.close(
            code=1008,
            reason="Player not in BROWSER mode. POST /api/player/play first.",
        )
        return

    # Switch OBS to the StreamScreen scene
    if _obs is not None and _obs.is_connected():
        try:
            await _obs.switch_to_stream_screen()
        except Exception as e:
            print(f"[ws/screen-share] failed to switch OBS scene: {e}")

    print("[ws/screen-share] browser connected, receiving frames")
    frame_count = 0

    try:
        while True:
            message = await websocket.receive()

            if "bytes" in message and message["bytes"]:
                try:
                    _player.push_browser_frame(message["bytes"])
                except Exception as e:
                    print(f"[ws/screen-share] frame {frame_count} error: {e}")
                frame_count += 1

            elif "text" in message and message["text"]:
                try:
                    cmd = json.loads(message["text"])
                    action = cmd.get("action", "")
                    if action == "stop":
                        print(
                            f"[ws/screen-share] browser requested stop "
                            f"after {frame_count} frames"
                        )
                        _player.stop()
                        break
                except json.JSONDecodeError:
                    pass

    except (WebSocketDisconnect, RuntimeError, Exception) as e:
        print(
            f"[ws/screen-share] browser disconnected after {frame_count} frames"
            f" ({type(e).__name__}: {e})"
        )
        if _player.is_browser_active:
            _player.stop()
    finally:
        # Schedule delayed revert to previous scene
        if _obs is not None and _obs.is_connected():
            try:
                await _obs.schedule_revert_scene()
            except Exception as e:
                print(f"[ws/screen-share] failed to schedule scene revert: {e}")
