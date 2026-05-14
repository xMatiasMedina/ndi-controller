"""
A tiny in-process async event bus.

The player service publishes state changes here. The WebSocket route subscribes
and forwards events to the dashboard. This is the seam that lets us add new
consumers (logging, metrics, scripted automation) without touching the player.
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Dict, List

EventHandler = Callable[[str, Any], Awaitable[None]]


class EventBus:
    def __init__(self) -> None:
        self._subscribers: Dict[str, List[EventHandler]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Called once at app startup so worker threads can publish via this loop."""
        self._loop = loop

    def subscribe(self, event: str, handler: EventHandler) -> None:
        self._subscribers.setdefault(event, []).append(handler)

    def unsubscribe(self, event: str, handler: EventHandler) -> None:
        if event in self._subscribers:
            try:
                self._subscribers[event].remove(handler)
            except ValueError:
                pass

    async def publish(self, event: str, payload: Any) -> None:
        for handler in list(self._subscribers.get(event, [])):
            try:
                await handler(event, payload)
            except Exception as e:
                # Never let a bad subscriber take down the publisher.
                print(f"[event_bus] handler for '{event}' raised: {e}")

    def publish_sync(self, event: str, payload: Any) -> None:
        """Schedule a publish from non-async code (the streamer threads)."""
        if self._loop is None:
            return  # bus not yet attached, drop the event
        asyncio.run_coroutine_threadsafe(self.publish(event, payload), self._loop)


# A single bus instance for the app
event_bus = EventBus()


# Event name constants — refer to these instead of magic strings
class Events:
    PLAYER_STATE_CHANGED = "player.state_changed"
    TRACK_STARTED = "player.track_started"
    TRACK_ENDED = "player.track_ended"
    POSITION_CHANGED = "player.position_changed"
    OBS_STATUS_CHANGED = "obs.status_changed"
    REAPER_STATUS_CHANGED = "reaper.status_changed"
    NDI_STATUS_CHANGED = "ndi.status_changed"
