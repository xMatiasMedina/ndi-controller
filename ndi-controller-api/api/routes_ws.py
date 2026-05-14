"""
WebSocket routes:

1. /ws/state — broadcasts player/OBS/Reaper state changes to the dashboard.
   The route subscribes to the event bus and forwards each event as a JSON
   message of the form: {"event": "...", "payload": {...}}.

2. /ws/screen-share — receives JPEG frames from the browser's getDisplayMedia
   capture and pushes them into the active BrowserSource via PlayerService.
   Binary messages = JPEG frame data. Text messages = JSON control commands.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from core.events import Events, event_bus
from services.player_service import PlayerService

router = APIRouter()


# ----- Dependency placeholder (overridden by main.py) -----
def get_player() -> PlayerService:
    raise NotImplementedError


# Keep a module-level reference set by main.py's dependency override
_player: PlayerService | None = None


def set_player_ref(player: PlayerService) -> None:
    """Called by main.py after wiring to give this module a direct reference."""
    global _player
    _player = player


# ----- Outbound: state broadcast -----
@router.websocket("/ws/state")
async def state_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    queue: asyncio.Queue = asyncio.Queue()

    async def handler(event: str, payload: Any) -> None:
        await queue.put({"event": event, "payload": payload})

    # Subscribe to the relevant events
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
    except WebSocketDisconnect:
        pass
    finally:
        for ev in events_to_forward:
            event_bus.unsubscribe(ev, handler)


# ----- Inbound: browser screen share -----
@router.websocket("/ws/screen-share")
async def screen_share_socket(websocket: WebSocket) -> None:
    """
    Receives frames from the browser's screen capture.

    Protocol:
      - Binary message: raw JPEG frame data → pushed to BrowserSource
      - Text message: JSON control, e.g. {"action": "stop"}
      - Close: browser stopped sharing

    The frontend should:
      1. POST /api/player/play { mode: "browser" }
      2. Open this WebSocket
      3. Use getDisplayMedia() + canvas to grab frames
      4. Send each frame as a binary WebSocket message (JPEG blob)
      5. On user stop: send {"action": "stop"} then close the socket
    """
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

    print("[ws/screen-share] browser connected, receiving frames")
    frame_count = 0

    try:
        while True:
            message = await websocket.receive()

            if "bytes" in message and message["bytes"]:
                # Binary = JPEG frame
                _player.push_browser_frame(message["bytes"])
                frame_count += 1

            elif "text" in message and message["text"]:
                # Text = JSON control command
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

    except WebSocketDisconnect:
        print(
            f"[ws/screen-share] browser disconnected after {frame_count} frames"
        )
        # Auto-stop playback when the browser drops the connection
        if _player.is_browser_active:
            _player.stop()